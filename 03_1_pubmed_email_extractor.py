from Bio import Entrez
import pandas as pd
import re
import time

from config import (
    NCBI_EMAIL,
    PAPERS_SCORED_FILE,
    AUTHOR_EMAIL_CANDIDATES_FILE,
    AUTHOR_EMAIL_SUMMARY_FILE,
)

Entrez.email = NCBI_EMAIL

INPUT_FILE = PAPERS_SCORED_FILE
OUTPUT_FILE = AUTHOR_EMAIL_CANDIDATES_FILE
SUMMARY_FILE = AUTHOR_EMAIL_SUMMARY_FILE

BATCH_SIZE = 20
MAX_RETRIES = 3
RETRY_DELAY = 3


def extract_emails(text):
    if not text:
        return []

    emails = re.findall(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        text,
    )

    cleaned = []

    for email in emails:
        email = email.strip().strip(".,;:()[]{}<>").lower()

        bad_endings = [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"]
        bad_terms = ["noreply", "no-reply", "example", "support", "webmaster", "privacy"]

        if any(email.endswith(x) for x in bad_endings):
            continue
        if any(term in email for term in bad_terms):
            continue

        if email not in cleaned:
            cleaned.append(email)

    return cleaned


def get_author_name(author):
    last_name = author.get("LastName", "")
    fore_name = author.get("ForeName", "")
    collective_name = author.get("CollectiveName", "")

    full_name = f"{fore_name} {last_name}".strip()
    return full_name or collective_name


def safe_get_year(article_info):
    try:
        return article_info["Journal"]["JournalIssue"]["PubDate"]["Year"]
    except Exception:
        return ""


def fetch_pubmed_batch(batch_pmids, batch_label):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            fetch = Entrez.efetch(
                db="pubmed",
                id=",".join(batch_pmids),
                rettype="abstract",
                retmode="xml",
            )
            records = Entrez.read(fetch)
            fetch.close()
            return records

        except Exception as e:
            print(f"  Fetch failed for batch {batch_label}, attempt {attempt}/{MAX_RETRIES}: {e}")
            time.sleep(RETRY_DELAY)

    print(f"  Skipping batch {batch_label} after {MAX_RETRIES} failed attempts.")
    return None


def main():
    df = pd.read_csv(INPUT_FILE)

    if "PMID" not in df.columns:
        raise ValueError(f"{INPUT_FILE} must contain a PMID column.")

    pmids = (
        df["PMID"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    print(f"Found {len(pmids)} PMIDs")

    rows = []
    skipped_batches = 0

    for start in range(0, len(pmids), BATCH_SIZE):
        batch_pmids = pmids[start:start + BATCH_SIZE]
        batch_label = f"{start + 1}-{start + len(batch_pmids)}"

        print(f"Fetching {batch_label}")

        records = fetch_pubmed_batch(batch_pmids, batch_label)

        if records is None:
            skipped_batches += 1
            continue

        for article in records.get("PubmedArticle", []):
            citation = article.get("MedlineCitation", {})
            article_info = citation.get("Article", {})

            pmid = str(citation.get("PMID", ""))
            title = str(article_info.get("ArticleTitle", ""))

            try:
                journal = str(article_info["Journal"].get("Title", ""))
            except Exception:
                journal = ""

            year = safe_get_year(article_info)

            try:
                author_list = article_info["AuthorList"]
            except Exception:
                author_list = []

            for author in author_list:
                author_name = get_author_name(author)

                if not author_name:
                    continue

                affiliations = []

                try:
                    affiliation_info = author.get("AffiliationInfo", [])
                    for aff in affiliation_info:
                        aff_text = aff.get("Affiliation", "")
                        if aff_text:
                            affiliations.append(aff_text)
                except Exception:
                    pass

                affiliation_text = " || ".join(affiliations)
                emails = extract_emails(affiliation_text)

                if emails:
                    rows.append({
                        "Author": author_name,
                        "Email Candidates": "; ".join(emails),
                        "PMID": pmid,
                        "Year": year,
                        "Title": title,
                        "Journal": journal,
                        "Affiliation": affiliation_text,
                    })

        time.sleep(0.5)

    expected_cols = [
        "Author",
        "Email Candidates",
        "PMID",
        "Year",
        "Title",
        "Journal",
        "Affiliation",
    ]

    email_df = pd.DataFrame(rows)
    if email_df.empty:
        email_df = pd.DataFrame(columns=expected_cols)
    else:
        email_df = email_df[expected_cols]

    email_df.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Saved {OUTPUT_FILE}")
    print(email_df.head(20))

    if len(email_df) > 0:
        author_email_summary = (
            email_df
            .groupby("Author")
            .agg(
                Email_Candidates=("Email Candidates", lambda x: "; ".join(sorted(set("; ".join(x).split("; "))))),
                Email_Source_PMIDs=("PMID", lambda x: "; ".join(sorted(set(str(i) for i in x)))),
                Example_Affiliation=("Affiliation", lambda x: " || ".join(list(x)[:2])),
            )
            .reset_index()
        )
    else:
        author_email_summary = pd.DataFrame(
            columns=["Author", "Email_Candidates", "Email_Source_PMIDs", "Example_Affiliation"]
        )

    author_email_summary.to_csv(
        SUMMARY_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Saved {SUMMARY_FILE}")
    print(author_email_summary.head(20))
    print(f"Skipped batches: {skipped_batches}")


if __name__ == "__main__":
    main()
