import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import pandas as pd
import requests

from config import (
    NCBI_EMAIL,
    PAPERS_SCORED_FILE,
    AUTHOR_AGG_V2_FILE,
    AUTHOR_EMAIL_SUMMARY_FILE,
    AUTHOR_EMAIL_CANDIDATES_FILE,
    AUTHOR_AFFILIATION_SUMMARY_FILE,
    AUTHOR_MASTER_FILE,
    FINAL_XLSX,
)

AUTHOR_AGG = AUTHOR_AGG_V2_FILE
PAPERS_FILE = Path(PAPERS_SCORED_FILE)
RUN_DIR = PAPERS_FILE.parent

AFFILIATION_SUMMARY = Path(AUTHOR_AFFILIATION_SUMMARY_FILE)
PUBMED_SUMMARY = Path(AUTHOR_EMAIL_SUMMARY_FILE)
PUBMED_CANDIDATES = Path(AUTHOR_EMAIL_CANDIDATES_FILE)
PUBLISHER_EMAILS = RUN_DIR / "publisher_corresponding_emails.csv"

MASTER_CSV = AUTHOR_MASTER_FILE

BATCH_SIZE = 100
DELAY = 0.35

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_EMAIL_PARTS = [
    "noreply", "no-reply", "example", "support", "webmaster", "privacy",
    "admin@", "info@", "newsletter", "media@", "press@", "careers@", "jobs@", "help@"
]
GENERIC_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com"}
LOW_TRUST_DOMAINS = GENERIC_DOMAINS | {"qq.com", "126.com", "163.com", "sina.com", "yeah.net"}


def clean(x):
    return "" if pd.isna(x) else str(x).strip()


def norm_ascii(text):
    text = unicodedata.normalize("NFKD", clean(text))
    return "".join(c for c in text if not unicodedata.combining(c))


def norm_name(name):
    name = norm_ascii(name)
    name = re.sub(r"[^A-Za-z\s\-]", " ", name)
    name = re.sub(r"\s+", " ", name).strip().lower()
    return name


def name_parts(name):
    parts = norm_name(name).split()
    if not parts:
        return "", "", ""
    return parts[0], "".join(parts[1:-1]), parts[-1]


def name_signature(name):
    first, _, last = name_parts(name)
    return f"{last}|{first[:1]}" if first and last else norm_name(name)


def extract_emails(text):
    out = []
    for e in EMAIL_RE.findall(clean(text)):
        e = e.strip().strip(".,;:()[]{}<>").lower()
        if any(bad in e for bad in BAD_EMAIL_PARTS):
            continue
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            continue
        if e not in out:
            out.append(e)
    return out


def email_domain(email):
    return email.split("@")[-1].lower() if "@" in email else ""


def email_name_match_score(author, email):
    first, _, last = name_parts(author)
    if not first or not last or "@" not in email:
        return 0

    local = email.split("@")[0].lower().replace("-", ".").replace("_", ".")
    compact = local.replace(".", "")
    score = 0

    if local == f"{first}.{last}":
        score += 20
    if local == f"{first[0]}.{last}":
        score += 18
    if compact == f"{first}{last}":
        score += 18
    if compact == f"{first[0]}{last}":
        score += 16
    if last in local:
        score += 10
    if first in local:
        score += 6
    if compact.startswith(first[0] + last[:4]):
        score += 5
    if email_domain(email) not in LOW_TRUST_DOMAINS:
        score += 2

    return score


def choose_best_email(author, emails):
    emails = list(dict.fromkeys([e for e in emails if "@" in e]))
    if not emails:
        return "", 0

    scored = [(e, email_name_match_score(author, e)) for e in emails]
    scored = sorted(scored, key=lambda x: x[1], reverse=True)
    return scored[0]


def safe_text(el):
    return "" if el is None else "".join(el.itertext()).strip()


def get_author_name(author_el):
    fore = safe_text(author_el.find("ForeName"))
    last = safe_text(author_el.find("LastName"))
    collective = safe_text(author_el.find("CollectiveName"))
    return f"{fore} {last}".strip() or collective


def build_affiliation_summary_if_possible():
    if AFFILIATION_SUMMARY.exists():
        return

    if not PAPERS_FILE.exists():
        print(f"Warning: {AFFILIATION_SUMMARY} and {PAPERS_FILE} not found.")
        return

    papers = pd.read_csv(PAPERS_FILE)
    if "PMID" not in papers.columns or papers.empty:
        return

    pmids = papers["PMID"].dropna().astype(str).drop_duplicates().tolist()
    rows = []

    for start in range(0, len(pmids), BATCH_SIZE):
        batch = pmids[start:start + BATCH_SIZE]
        print(f"  PubMed fetch {start + 1}-{start + len(batch)}")

        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        params = {"db": "pubmed", "id": ",".join(batch), "retmode": "xml", "email": NCBI_EMAIL}

        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            root = ET.fromstring(r.text)
        except Exception as e:
            print(f"  Fetch failed: {e}")
            continue

        for article in root.findall(".//PubmedArticle"):
            pmid = safe_text(article.find(".//PMID"))
            title = safe_text(article.find(".//ArticleTitle"))

            for a in article.findall(".//Author"):
                author = get_author_name(a)
                if not author:
                    continue

                affs = [safe_text(aff) for aff in a.findall(".//AffiliationInfo/Affiliation")]
                affs = [x for x in affs if x]
                aff_text = " || ".join(affs)

                rows.append({
                    "Author": author,
                    "PMID": pmid,
                    "Title": title,
                    "Affiliation": aff_text,
                    "Emails_In_Affiliation": "; ".join(extract_emails(aff_text)),
                })

        time.sleep(DELAY)

    if rows:
        out = pd.DataFrame(rows)
        summary = out.groupby("Author", as_index=False).agg(
            Affiliation_Text=("Affiliation", lambda x: " || ".join([clean(v) for v in x if clean(v)][:5])),
            Email_Candidates=("Emails_In_Affiliation", lambda x: "; ".join(sorted(set(extract_emails(" || ".join(map(clean, x))))))),
            Source_PMIDs=("PMID", lambda x: "; ".join(sorted(set(map(str, x)))[:10])),
        )
        summary.to_csv(AFFILIATION_SUMMARY, index=False, encoding="utf-8-sig")


def add_record(records_exact, records_sig, source, row):
    author = clean(row.get("Author", ""))
    if not author:
        return

    row_text = " || ".join(clean(v) for v in row.values)
    emails = extract_emails(row_text)
    exact = norm_name(author)
    sig = name_signature(author)

    for records, key in [(records_exact, exact), (records_sig, sig)]:
        records[key]["texts"].append(row_text)
        for e in emails:
            records[key]["emails"].append(e)
            records[key]["evidence"].append(f"{source}: {e}")


def load_sources():
    records_exact = defaultdict(lambda: {"emails": [], "texts": [], "evidence": []})
    records_sig = defaultdict(lambda: {"emails": [], "texts": [], "evidence": []})

    for label, path in [
        ("affiliation_summary", AFFILIATION_SUMMARY),
        ("pubmed_summary", PUBMED_SUMMARY),
        ("pubmed_candidates", PUBMED_CANDIDATES),
    ]:
        if not Path(path).exists():
            continue

        try:
            sdf = pd.read_csv(path)
        except Exception as e:
            print(f"Warning: could not read {path}: {e}")
            continue

        if "Author" not in sdf.columns:
            continue

        for _, row in sdf.iterrows():
            add_record(records_exact, records_sig, label, row)

    return records_exact, records_sig


def load_publisher_email_map():
    """
    Returns PMID -> publisher email rows.
    These are preferred over PubMed affiliation emails because they are closer to
    corresponding-author information from publisher pages.
    """
    if not PUBLISHER_EMAILS.exists():
        return {}

    try:
        df = pd.read_csv(PUBLISHER_EMAILS)
    except Exception:
        return {}

    if df.empty or "PMID" not in df.columns or "Corresponding_Email" not in df.columns:
        return {}

    out = defaultdict(list)
    for _, row in df.iterrows():
        email = clean(row.get("Corresponding_Email", "")).lower()
        if "@" not in email:
            continue

        pmid = clean(row.get("PMID", ""))
        out[pmid].append({
            "email": email,
            "author": clean(row.get("Corresponding_Author", "")),
            "source": clean(row.get("Email_Source", "")),
            "confidence": clean(row.get("Confidence", "")),
            "url": clean(row.get("Resolved_URL", "")) or clean(row.get("Publisher_URL", "")),
        })

    return out


def collect_email_candidates(df, records_exact, records_sig):
    all_rows = []
    candidate_map = {}
    evidence_map = {}

    for _, row in df.iterrows():
        author = clean(row.get("Author", ""))
        exact = norm_name(author)
        sig = name_signature(author)

        row_text = " || ".join(clean(v) for v in row.values)
        all_emails = extract_emails(row_text)
        evidence = []

        for rec in [records_exact.get(exact, {}), records_sig.get(sig, {})]:
            all_emails.extend(rec.get("emails", []))
            evidence.extend(rec.get("evidence", []))

        all_emails = list(dict.fromkeys(all_emails))
        candidate_map[author] = all_emails
        evidence_map[author] = " || ".join(list(dict.fromkeys(evidence))[:8])

        for email in all_emails:
            all_rows.append({
                "Author": author,
                "Email": email,
                "Institution": clean(row.get("Institution", "")),
                "Email_Match_Score": email_name_match_score(author, email),
            })

    usage_df = pd.DataFrame(all_rows)
    if usage_df.empty:
        return candidate_map, evidence_map, {}, {}

    usage = usage_df.groupby("Email")["Author"].nunique().to_dict()
    institution_usage = usage_df.groupby("Email")["Institution"].nunique().to_dict()
    return candidate_map, evidence_map, usage, institution_usage


def publisher_email_for_author(row, publisher_map):
    """
    Only assign publisher email to a specific author if:
    - the publisher corresponding author text matches the author name, OR
    - exactly one publisher email exists for one of the author's representative PMIDs.
    Otherwise keep it for review.
    """
    author = clean(row.get("Author", ""))
    pmids = clean(row.get("Representative_PMIDs", ""))

    pmid_list = [p.strip() for p in re.split(r";|\|", pmids) if p.strip()]

    candidates = []
    for pmid in pmid_list:
        candidates.extend(publisher_map.get(pmid, []))

    if not candidates:
        return "", "", "", ""

    candidates = list({c["email"]: c for c in candidates}.values())

    # If corresponding author string matches author name, this is best.
    for c in candidates:
        corr_author = norm_name(c.get("author", ""))
        current_author = norm_name(author)
        if corr_author and current_author and (corr_author in current_author or current_author in corr_author):
            return (
                c["email"],
                "publisher_corresponding_author_email",
                "",
                f'{c.get("source", "")}; {c.get("url", "")}',
            )

    # If only one corresponding email exists for the representative article(s), it is useful but still not fully author-confirmed.
    if len(candidates) == 1:
        c = candidates[0]
        return (
            c["email"],
            "publisher_corresponding_email_article_level",
            "",
            f'{c.get("source", "")}; {c.get("url", "")}',
        )

    # Multiple publisher emails: show for review, do not assign to Best Email.
    review = "; ".join(c["email"] for c in candidates)
    evidence = "multiple publisher emails found for representative PMIDs"
    return "", "publisher_email_needs_review", review, evidence


def classify_affiliation_email_for_author(author, email, usage, institution_usage):
    if not email:
        return {"best_email": "", "status": "missing", "review_email": "", "score": 0}

    score = email_name_match_score(author, email)
    domain = email_domain(email)
    author_count = usage.get(email, 0)
    institution_count = institution_usage.get(email, 0)

    if author_count > 1:
        return {
            "best_email": "",
            "status": f"shared_email_needs_review; authors={author_count}; institutions={institution_count}",
            "review_email": email,
            "score": score,
        }

    if domain in LOW_TRUST_DOMAINS and score < 12:
        return {
            "best_email": "",
            "status": "affiliation_email_needs_review_low_name_match",
            "review_email": email,
            "score": score,
        }

    if score >= 12:
        return {
            "best_email": email,
            "status": "author_matched_affiliation_email",
            "review_email": "",
            "score": score,
        }

    return {
        "best_email": "",
        "status": "affiliation_email_needs_review",
        "review_email": email,
        "score": score,
    }


def build_final_columns(df):
    keep_cols = [
        "Author",
        "Institution",
        "Department",
        "Affiliation",
        "Preferred_Email",
        "Preferred_Email_Status",
        "Email_To_Review",
        "Publisher_Email",
        "Publisher_Email_Evidence",
        "Email_Match_Score",
        "Email_Evidence",
        "All_Email_Candidates",
        "Author_Expertise_Score",
        "Relevant_Paper_Count",
        "Average_Article_Score",
        "Max_Article_Score",
        "Years",
        "ImmPro Interest Score",
        "Interest Signals",
        "Strategic Fit Score",
        "Representative_PMIDs",
        "Representative_Article_Titles",
        "Representative_PubMed_Links",
        "Representative_DOIs",
        "Journals",
        "Matched_Keywords",
        "Faculty Search Query",
        "Email Search Query",
        "Program Query",
        "Clinical Trial Query",
        "Industry Collaboration Query",
        "Google Scholar Query",
        "Manual Notes",
    ]

    final = df[[c for c in keep_cols if c in df.columns]].copy()

    return final.rename(columns={
        "Author": "Author Name",
        "Institution": "Institution / School",
        "Affiliation": "Raw Affiliation",
        "Preferred_Email": "Best Email",
        "Preferred_Email_Status": "Email Status",
        "Email_To_Review": "Email To Review",
        "Publisher_Email": "Publisher Corresponding Email",
        "Publisher_Email_Evidence": "Publisher Email Evidence",
        "Email_Match_Score": "Email Match Score",
        "All_Email_Candidates": "All Email Candidates",
        "Email_Evidence": "Email Evidence",
        "Author_Expertise_Score": "Author Expertise Score",
        "Relevant_Paper_Count": "Relevant Paper Count",
        "Average_Article_Score": "Average Article Score",
        "Max_Article_Score": "Max Article Score",
        "ImmPro Interest Score": "Outreach Signal Score",
        "Representative_PMIDs": "Representative PMIDs",
        "Representative_Article_Titles": "Representative Article Titles",
        "Representative_PubMed_Links": "PubMed Article Links",
        "Representative_DOIs": "DOI Links",
        "Matched_Keywords": "Matched Relevance Keywords",
    })


def main():
    build_affiliation_summary_if_possible()

    if not Path(AUTHOR_AGG).exists():
        raise FileNotFoundError(f"Missing {AUTHOR_AGG}")

    df = pd.read_csv(AUTHOR_AGG)

    if df.empty:
        df.to_csv(MASTER_CSV, index=False, encoding="utf-8-sig")
        with pd.ExcelWriter(FINAL_XLSX, engine="openpyxl") as writer:
            pd.DataFrame().to_excel(writer, sheet_name="Balanced Ranking", index=False)
        return

    records_exact, records_sig = load_sources()
    publisher_map = load_publisher_email_map()
    candidate_map, evidence_map, usage, institution_usage = collect_email_candidates(df, records_exact, records_sig)

    preferred_emails = []
    preferred_statuses = []
    review_emails = []
    publisher_emails = []
    publisher_evidence = []
    match_scores = []
    all_candidates = []
    evidence_lines = []

    for _, row in df.iterrows():
        author = clean(row.get("Author", ""))

        pub_email, pub_status, pub_review, pub_ev = publisher_email_for_author(row, publisher_map)

        candidates = candidate_map.get(author, [])
        best_candidate, _ = choose_best_email(author, candidates)

        aff_classified = classify_affiliation_email_for_author(
            author=author,
            email=best_candidate,
            usage=usage,
            institution_usage=institution_usage,
        )

        if pub_email:
            preferred_emails.append(pub_email)
            preferred_statuses.append(pub_status)
            review_emails.append(pub_review or aff_classified["review_email"])
        else:
            preferred_emails.append(aff_classified["best_email"])
            if pub_status == "publisher_email_needs_review":
                preferred_statuses.append(pub_status)
                review_emails.append(pub_review)
            else:
                preferred_statuses.append(aff_classified["status"])
                review_emails.append(aff_classified["review_email"])

        publisher_emails.append(pub_email)
        publisher_evidence.append(pub_ev)
        match_scores.append(aff_classified["score"])
        all_candidates.append("; ".join(candidates))
        evidence_lines.append(evidence_map.get(author, ""))

    df["Preferred_Email"] = preferred_emails
    df["Preferred_Email_Status"] = preferred_statuses
    df["Email_To_Review"] = review_emails
    df["Publisher_Email"] = publisher_emails
    df["Publisher_Email_Evidence"] = publisher_evidence
    df["Email_Match_Score"] = match_scores
    df["All_Email_Candidates"] = all_candidates
    df["Email_Evidence"] = evidence_lines

    final = build_final_columns(df)
    df.to_csv(MASTER_CSV, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(FINAL_XLSX, engine="openpyxl") as writer:
        if "Author Expertise Score" in final.columns:
            final.sort_values(by="Author Expertise Score", ascending=False).to_excel(
                writer,
                sheet_name="Top Experts",
                index=False,
            )

        if "Outreach Signal Score" in final.columns:
            final.sort_values(by="Outreach Signal Score", ascending=False).to_excel(
                writer,
                sheet_name="Top Outreach Fits",
                index=False,
            )

        if "Strategic Fit Score" in final.columns:
            final.sort_values(by="Strategic Fit Score", ascending=False).to_excel(
                writer,
                sheet_name="Balanced Ranking",
                index=False,
            )
        else:
            final.to_excel(writer, sheet_name="Balanced Ranking", index=False)

        if PAPERS_FILE.exists():
            papers = pd.read_csv(PAPERS_FILE)
            article_cols = [
                "PMID", "Title", "Journal", "Year", "DOI", "PubMed_Link",
                "DOI_Link", "Authors", "Relevance Score", "Matched Keywords"
            ]
            papers[[c for c in article_cols if c in papers.columns]].to_excel(
                writer,
                sheet_name="Article Details",
                index=False,
            )

        if PUBLISHER_EMAILS.exists():
            publisher_df = pd.read_csv(PUBLISHER_EMAILS)
            publisher_df.to_excel(
                writer,
                sheet_name="Publisher Emails",
                index=False,
            )

    print("DONE")
    print(f"Saved working file: {MASTER_CSV}")
    print(f"Saved final deliverable: {FINAL_XLSX}")
    print(f"Best emails: {final['Best Email'].astype(str).str.contains('@').sum() if 'Best Email' in final.columns else 0}")
    print(f"Emails needing review: {final['Email To Review'].astype(str).str.contains('@').sum() if 'Email To Review' in final.columns else 0}")


if __name__ == "__main__":
    main()
