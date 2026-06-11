import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests

from config import (
    PAPERS_SCORED_FILE,
)

PAPERS_FILE = Path(PAPERS_SCORED_FILE)
RUN_DIR = PAPERS_FILE.parent
OUTPUT_FILE = RUN_DIR / "publisher_corresponding_emails.csv"

REQUEST_DELAY = 0.8
TIMEOUT = 20
MAX_ARTICLES = 80

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r"mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})", re.I)

BAD_EMAIL_PARTS = [
    "noreply", "no-reply", "example", "support", "webmaster", "privacy",
    "admin@", "info@", "newsletter", "media@", "press@", "careers@", "jobs@",
    "help@", "editorial", "permissions", "rights", "advertising"
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def clean(x):
    if pd.isna(x):
        return ""
    return str(x).strip()


def extract_emails(text):
    if not text:
        return []

    emails = []
    for e in EMAIL_RE.findall(str(text)):
        e = e.strip().strip(".,;:()[]{}<>").lower()

        if any(bad in e for bad in BAD_EMAIL_PARTS):
            continue

        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf")):
            continue

        if e not in emails:
            emails.append(e)

    return emails


def emails_from_html(html):
    found = []

    for e in MAILTO_RE.findall(html or ""):
        e = e.strip().lower()
        if e not in found:
            found.append(e)

    for e in extract_emails(html):
        if e not in found:
            found.append(e)

    return found


def likely_corresponding_context(html, email):
    """
    Find nearby text around an email and classify whether it looks like a corresponding author email.
    """
    if not html or not email:
        return "", "publisher_email_found"

    lower = html.lower()
    idx = lower.find(email.lower())

    if idx == -1:
        return "", "publisher_email_found"

    start = max(0, idx - 700)
    end = min(len(html), idx + 700)
    context = re.sub(r"\s+", " ", html[start:end])
    context_lower = context.lower()

    strong_terms = [
        "correspondence", "corresponding author", "correspondence to",
        "corresponding authors", "for correspondence", "email address",
        "e-mail address", "author for correspondence"
    ]

    if any(term in context_lower for term in strong_terms):
        return context[:1000], "publisher_corresponding_email"

    return context[:1000], "publisher_email_found"


def fetch_url(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return "", r.url, f"http_{r.status_code}"
        return r.text, r.url, "ok"
    except Exception as e:
        return "", url, f"request_failed: {e}"


def candidate_urls(row):
    urls = []

    doi_link = clean(row.get("DOI_Link", ""))
    doi = clean(row.get("DOI", ""))
    pubmed_link = clean(row.get("PubMed_Link", ""))

    if doi_link:
        urls.append(doi_link)

    if doi and not doi_link:
        urls.append(f"https://doi.org/{doi}")

    # PubMed page sometimes links out, but emails are usually not there.
    # Keep it as fallback only.
    if pubmed_link:
        urls.append(pubmed_link)

    return list(dict.fromkeys(urls))


def guess_corresponding_author_from_context(context):
    """
    Best-effort extraction only. This is intentionally conservative.
    """
    if not context:
        return ""

    patterns = [
        r"Correspondence to\s+([^.;:<>\n]{2,100})",
        r"Corresponding author[s]?:?\s*([^.;:<>\n]{2,100})",
        r"For correspondence:?\s*([^.;:<>\n]{2,100})",
    ]

    for pat in patterns:
        m = re.search(pat, context, flags=re.I)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip()
            name = re.sub(r"email.*$", "", name, flags=re.I).strip()
            return name[:120]

    return ""


def write_empty():
    cols = [
        "PMID",
        "Title",
        "DOI",
        "Publisher_URL",
        "Resolved_URL",
        "Corresponding_Author",
        "Corresponding_Email",
        "Email_Source",
        "Confidence",
        "Context",
    ]
    pd.DataFrame(columns=cols).to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")


def main():
    if not PAPERS_FILE.exists():
        print(f"No papers file found: {PAPERS_FILE}")
        write_empty()
        return

    try:
        papers = pd.read_csv(PAPERS_FILE)
    except Exception as e:
        print(f"Could not read papers file: {e}")
        write_empty()
        return

    if papers.empty:
        print("No papers to process.")
        write_empty()
        return

    rows = []

    papers = papers.head(MAX_ARTICLES)

    for idx, row in papers.iterrows():
        pmid = clean(row.get("PMID", ""))
        title = clean(row.get("Title", ""))
        doi = clean(row.get("DOI", ""))

        urls = candidate_urls(row)
        if not urls:
            continue

        print(f"Checking publisher emails for PMID {pmid} ({idx + 1}/{len(papers)})")

        article_found = False

        for url in urls:
            html, resolved_url, status = fetch_url(url)

            if status != "ok" or not html:
                continue

            emails = emails_from_html(html)

            if not emails:
                continue

            article_found = True

            for email in emails:
                context, source = likely_corresponding_context(html, email)

                if source == "publisher_corresponding_email":
                    confidence = "high"
                else:
                    confidence = "medium"

                rows.append({
                    "PMID": pmid,
                    "Title": title,
                    "DOI": doi,
                    "Publisher_URL": url,
                    "Resolved_URL": resolved_url,
                    "Corresponding_Author": guess_corresponding_author_from_context(context),
                    "Corresponding_Email": email,
                    "Email_Source": source,
                    "Confidence": confidence,
                    "Context": context,
                })

            # If a DOI page gives emails, do not keep trying PubMed fallback.
            break

        if not article_found:
            pass

        time.sleep(REQUEST_DELAY)

    cols = [
        "PMID",
        "Title",
        "DOI",
        "Publisher_URL",
        "Resolved_URL",
        "Corresponding_Author",
        "Corresponding_Email",
        "Email_Source",
        "Confidence",
        "Context",
    ]

    out = pd.DataFrame(rows)
    if out.empty:
        out = pd.DataFrame(columns=cols)
    else:
        out = out[cols].drop_duplicates()

    out.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"Saved {OUTPUT_FILE}")
    print(f"Publisher emails found: {len(out)}")


if __name__ == "__main__":
    main()
