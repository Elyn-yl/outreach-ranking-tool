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
PAPERS_FILE = PAPERS_SCORED_FILE
AFFILIATION_SUMMARY = AUTHOR_AFFILIATION_SUMMARY_FILE
PUBMED_SUMMARY = AUTHOR_EMAIL_SUMMARY_FILE
PUBMED_CANDIDATES = AUTHOR_EMAIL_CANDIDATES_FILE
MASTER_CSV = AUTHOR_MASTER_FILE

BATCH_SIZE = 100
DELAY = 0.35

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_EMAIL_PARTS = [
    "noreply", "no-reply", "example", "support", "webmaster", "privacy",
    "admin@", "info@", "newsletter", "media@", "press@", "careers@", "jobs@", "help@",
    "editorial", "permissions", "rights", "advertising"
]

# These domains are not automatically wrong, but they are too risky for automated outreach
# unless the local-part strongly matches the author's name.
LOW_TRUST_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "qq.com", "126.com", "163.com", "sina.com", "yeah.net"
}


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
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf")):
            continue

        if e not in out:
            out.append(e)

    return out


def email_domain(email):
    return email.split("@")[-1].lower() if "@" in email else ""


def email_name_match_score(author, email):
    """
    Score how well the email local-part matches the author's name.
    This is not proof, but it prevents obviously wrong shared/corresponding emails
    from being assigned to unrelated coauthors.
    """
    first, _, last = name_parts(author)

    if not first or not last or "@" not in email:
        return 0

    local = email.split("@")[0].lower().replace("-", ".").replace("_", ".")
    compact = local.replace(".", "")
    score = 0

    if local == f"{first}.{last}":
        score += 30
    if local == f"{first[0]}.{last}":
        score += 28
    if compact == f"{first}{last}":
        score += 28
    if compact == f"{first[0]}{last}":
        score += 26

    if last in local:
        score += 12
    if first in local:
        score += 8
    if compact.startswith(first[0] + last[:4]):
        score += 8

    # Institutional domain gives a very small boost only after name evidence exists.
    if email_domain(email) not in LOW_TRUST_DOMAINS and score > 0:
        score += 2

    return score


def choose_best_email(author, emails):
    emails = list(dict.fromkeys([e for e in emails if "@" in e]))
    if not emails:
        return "", 0

    scored = [(e, email_name_match_score(author, e)) for e in emails]
    scored = sorted(scored, key=lambda x: x[1], reverse=True)
    return scored[0]


def is_safe_best_email(author, email, email_usage):
    """
    Strict rule:
    - email must appear for exactly one author in the aggregated dataset
    - local part must clearly match the author's first/last name
    This is intentionally conservative.
    """
    if not email or "@" not in email:
        return False

    if email_usage.get(email, 0) > 1:
        return False

    return email_name_match_score(author, email) >= 18


def safe_text(el):
    return "" if el is None else "".join(el.itertext()).strip()


def get_author_name(author_el):
    fore = safe_text(author_el.find("ForeName"))
    last = safe_text(author_el.find("LastName"))
    collective = safe_text(author_el.find("CollectiveName"))
    return f"{fore} {last}".strip() or collective


def build_affiliation_summary_if_possible():
    if Path(AFFILIATION_SUMMARY).exists():
        return

    if not Path(PAPERS_FILE).exists():
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


def collect_email_candidates(df, records_exact, records_sig):
    candidate_map = {}
    evidence_map = {}
    email_to_authors = defaultdict(set)

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
            email_to_authors[email].add(author)

    email_usage = {email: len(authors) for email, authors in email_to_authors.items()}

    return candidate_map, evidence_map, email_usage


def build_email_columns(df, candidate_map, evidence_map, email_usage):
    best_emails = []
    statuses = []
    review_emails = []
    match_scores = []

    for _, row in df.iterrows():
        author = clean(row.get("Author", ""))
        candidates = candidate_map.get(author, [])
        best_candidate, score = choose_best_email(author, candidates)

        if is_safe_best_email(author, best_candidate, email_usage):
            best_emails.append(best_candidate)
            statuses.append("author_matched_unique_email")
            review_emails.append("")
            match_scores.append(score)
        else:
            best_emails.append("")
            match_scores.append(score)

            if best_candidate:
                review_emails.append(best_candidate)
                if email_usage.get(best_candidate, 0) > 1:
                    statuses.append(f"shared_or_corresponding_email_not_assigned; authors={email_usage.get(best_candidate, 0)}")
                elif score < 18:
                    statuses.append("weak_name_match_not_assigned")
                else:
                    statuses.append("email_not_assigned_needs_review")
            else:
                review_emails.append("")
                statuses.append("missing")

    df["Preferred_Email"] = best_emails
    df["Preferred_Email_Status"] = statuses
    df["Email_To_Review"] = review_emails
    df["Email_Match_Score"] = match_scores
    df["Email_Evidence"] = df["Author"].apply(lambda a: evidence_map.get(clean(a), ""))

    return df


def build_final_columns(df):
    keep_cols = [
        "Author",
        "Institution",
        "Department",
        "Affiliation",
        "Preferred_Email",
        "Preferred_Email_Status",
        "Email_To_Review",
        "Email_Match_Score",
        "Email_Evidence",
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
        "Email_Match_Score": "Email Match Score",
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
    candidate_map, evidence_map, email_usage = collect_email_candidates(df, records_exact, records_sig)
    df = build_email_columns(df, candidate_map, evidence_map, email_usage)

    final = build_final_columns(df)
    df.to_csv(MASTER_CSV, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(FINAL_XLSX, engine="openpyxl") as writer:
        if "Author Expertise Score" in final.columns:
            final.sort_values(by="Author Expertise Score", ascending=False).to_excel(
                writer, sheet_name="Top Experts", index=False
            )

        if "Outreach Signal Score" in final.columns:
            final.sort_values(by="Outreach Signal Score", ascending=False).to_excel(
                writer, sheet_name="Top Outreach Fits", index=False
            )

        if "Strategic Fit Score" in final.columns:
            final.sort_values(by="Strategic Fit Score", ascending=False).to_excel(
                writer, sheet_name="Balanced Ranking", index=False
            )
        else:
            final.to_excel(writer, sheet_name="Balanced Ranking", index=False)

        if Path(PAPERS_FILE).exists():
            papers = pd.read_csv(PAPERS_FILE)
            article_cols = [
                "PMID", "Title", "Journal", "Year", "DOI", "PubMed_Link",
                "DOI_Link", "Authors", "Relevance Score", "Matched Keywords"
            ]
            papers[[c for c in article_cols if c in papers.columns]].to_excel(
                writer, sheet_name="Article Details", index=False
            )

    print("DONE")
    print(f"Saved working file: {MASTER_CSV}")
    print(f"Saved final deliverable: {FINAL_XLSX}")
    print(f"Best emails: {final['Best Email'].astype(str).str.contains('@').sum() if 'Best Email' in final.columns else 0}")
    print(f"Emails needing review: {final['Email To Review'].astype(str).str.contains('@').sum() if 'Email To Review' in final.columns else 0}")


if __name__ == "__main__":
    main()
