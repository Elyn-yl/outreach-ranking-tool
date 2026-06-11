import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict, Counter
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
    "admin@", "info@", "newsletter", "media@", "press@", "careers@", "jobs@", "help@"
]
GENERIC_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com"}


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


def choose_best_email(author, emails):
    first, _, last = name_parts(author)
    emails = list(dict.fromkeys(emails))

    def score(e):
        local = e.split("@")[0].lower().replace("-", ".").replace("_", ".")
        compact = local.replace(".", "")
        s = 0
        if last and last in local:
            s += 10
        if first and first in local:
            s += 6
        if first and last and compact.startswith(first[0] + last[:4]):
            s += 5
        if first and last and local == f"{first}.{last}":
            s += 5
        if email_domain(e) not in GENERIC_DOMAINS:
            s += 2
        return s

    return sorted(emails, key=score, reverse=True)[0] if emails else ""


def classify_pattern(author, email):
    first, middle, last = name_parts(author)
    if not first or not last or "@" not in email:
        return "unknown"

    local = email.split("@")[0].lower().replace("_", ".").replace("-", ".")
    compact = local.replace(".", "")

    exact = {
        "first.last": f"{first}.{last}",
        "first_initial.last": f"{first[0]}.{last}",
        "first_initiallast": f"{first[0]}{last}",
        "firstlast": f"{first}{last}",
        "last.first": f"{last}.{first}",
        "first": first,
        "last": last,
    }

    if middle:
        exact["first.middle.last"] = f"{first}.{middle}.{last}"
        exact["first.middle_initial.last"] = f"{first}.{middle[0]}.{last}"

    for pat, expected in exact.items():
        if local == expected or compact == expected.replace(".", ""):
            return pat

    for n in range(min(len(last), 12), 3, -1):
        if compact == f"{first[0]}{last[:n]}":
            return f"first_initiallast_prefix{n}"

    for n in range(min(len(last), 12), 3, -1):
        if compact == f"{first}{last[:n]}":
            return f"firstlast_prefix{n}"

    return "unknown"


def generate_email(author, domain, pattern):
    first, middle, last = name_parts(author)
    if not first or not last or not domain:
        return ""

    if pattern == "first.last":
        local = f"{first}.{last}"
    elif pattern == "first_initial.last":
        local = f"{first[0]}.{last}"
    elif pattern == "first_initiallast":
        local = f"{first[0]}{last}"
    elif pattern == "firstlast":
        local = f"{first}{last}"
    elif pattern == "last.first":
        local = f"{last}.{first}"
    elif pattern == "first":
        local = first
    elif pattern == "last":
        local = last
    elif pattern == "first.middle.last" and middle:
        local = f"{first}.{middle}.{last}"
    elif pattern == "first.middle_initial.last" and middle:
        local = f"{first}.{middle[0]}.{last}"
    elif pattern.startswith("first_initiallast_prefix"):
        n = int(pattern.replace("first_initiallast_prefix", ""))
        local = f"{first[0]}{last[:n]}"
    elif pattern.startswith("firstlast_prefix"):
        n = int(pattern.replace("firstlast_prefix", ""))
        local = f"{first}{last[:n]}"
    else:
        return ""

    return f"{local}@{domain}"


def confidence_rank(conf):
    text = clean(conf).lower()
    if text.startswith("high"):
        return 3
    if text.startswith("medium"):
        return 2
    if text.startswith("low"):
        return 1
    if "confirmed" in text:
        return 4
    return 0


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


def build_final_columns(df):
    preferred = []
    status = []
    review_email = []

    for _, row in df.iterrows():
        direct = clean(row.get("Email", ""))
        inferred_email = clean(row.get("Inferred_Email", ""))
        inferred_conf = clean(row.get("Inferred_Email_Confidence", ""))

        if "@" in direct:
            preferred.append(direct)
            status.append("confirmed_from_pubmed_affiliation")
            review_email.append("")
        elif "@" in inferred_email and confidence_rank(inferred_conf) >= 3:
            preferred.append(inferred_email)
            status.append("inferred_high_confidence")
            review_email.append("")
        elif "@" in inferred_email:
            preferred.append("")
            status.append("inferred_needs_review")
            review_email.append(inferred_email)
        else:
            preferred.append("")
            status.append("missing")
            review_email.append("")

    df["Preferred_Email"] = preferred
    df["Preferred_Email_Status"] = status
    df["Email_To_Review"] = review_email

    keep_cols = [
        "Author",
        "Institution",
        "Department",
        "Affiliation",
        "Preferred_Email",
        "Preferred_Email_Status",
        "Email_To_Review",
        "Email",
        "Email_Evidence",
        "Inferred_Email",
        "Inferred_Email_Confidence",
        "Inferred_Email_Pattern",
        "Inferred_Email_Evidence",
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
        "Email": "Confirmed Email",
        "Email_Evidence": "Email Evidence",
        "Inferred_Email": "Inferred Email",
        "Inferred_Email_Confidence": "Inferred Email Confidence",
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

    emails = []
    email_evidence = []

    for _, row in df.iterrows():
        author = clean(row.get("Author", ""))
        exact = norm_name(author)
        sig = name_signature(author)

        row_text = " || ".join(clean(v) for v in row.values)
        all_emails = extract_emails(row_text)
        ev = []

        for rec in [records_exact.get(exact, {}), records_sig.get(sig, {})]:
            all_emails.extend(rec.get("emails", []))
            ev.extend(rec.get("evidence", []))

        best = choose_best_email(author, all_emails)
        emails.append(best)

        evidence_line = []
        if best:
            evidence_line.append(f"direct_or_source_text: {best}")
        evidence_line.extend(list(dict.fromkeys(ev))[:6])
        email_evidence.append(" || ".join(evidence_line))

    df["Email"] = emails
    df["Email_Evidence"] = email_evidence

    # Conservative inference placeholder: do not promote guessed emails unless future sources support it.
    df["Inferred_Email"] = ""
    df["Inferred_Email_Pattern"] = ""
    df["Inferred_Email_Confidence"] = "not_inferred"
    df["Inferred_Email_Evidence"] = ""

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

        if Path(PAPERS_FILE).exists():
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

    print("DONE")
    print(f"Saved working file: {MASTER_CSV}")
    print(f"Saved final deliverable: {FINAL_XLSX}")
    print(f"Confirmed emails: {df['Email'].astype(str).str.contains('@').sum()}")


if __name__ == "__main__":
    main()
