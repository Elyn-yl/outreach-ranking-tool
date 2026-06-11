import re
import time
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

import pandas as pd
import requests

from config import (
    AUTHOR_AGG_V2_FILE,
    PAPERS_SCORED_FILE,
)

AUTHOR_FILE = Path(AUTHOR_AGG_V2_FILE)
RUN_DIR = Path(PAPERS_SCORED_FILE).parent
OUTPUT_FILE = RUN_DIR / "faculty_email_candidates.csv"

MAX_AUTHORS = 60
MAX_SEARCH_RESULTS = 5
MAX_PAGES_PER_AUTHOR = 3
REQUEST_TIMEOUT = 8
REQUEST_DELAY = 0.25

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_EMAIL_PARTS = [
    "noreply", "no-reply", "example", "support", "webmaster", "privacy",
    "admin@", "info@", "newsletter", "media@", "press@", "careers@", "jobs@",
    "help@", "editorial", "permissions", "rights", "advertising", "billing"
]
LOW_VALUE_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "instagram.com",
    "youtube.com", "wikipedia.org", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"
}

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


def norm_name(name):
    name = clean(name)
    name = re.sub(r"[^A-Za-z\s\-]", " ", name)
    name = re.sub(r"\s+", " ", name).strip().lower()
    return name


def name_parts(name):
    parts = norm_name(name).split()
    if not parts:
        return "", "", ""
    return parts[0], "".join(parts[1:-1]), parts[-1]


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

    return score


def extract_emails(text):
    emails = []
    for e in EMAIL_RE.findall(str(text or "")):
        e = e.strip().strip(".,;:()[]{}<>").lower()
        if any(bad in e for bad in BAD_EMAIL_PARTS):
            continue
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf")):
            continue
        if e not in emails:
            emails.append(e)
    return emails


def is_bad_url(url):
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        return True

    if not domain:
        return True

    if any(bad in domain for bad in LOW_VALUE_DOMAINS):
        return True

    return False


def decode_duckduckgo_href(href):
    if not href:
        return ""

    href = href.replace("&amp;", "&")

    if href.startswith("//"):
        href = "https:" + href

    if "uddg=" in href:
        try:
            qs = parse_qs(urlparse(href).query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        except Exception:
            pass

    if href.startswith("http"):
        return href

    return ""


def search_web(query):
    """
    Free/no-API search using DuckDuckGo HTML endpoint.
    This may occasionally be rate-limited. If it fails, the script skips gracefully.
    """
    url = "https://html.duckduckgo.com/html/"
    params = {"q": query}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code >= 400:
            return []
        html = r.text
    except Exception:
        return []

    hrefs = re.findall(r'href="([^"]+)"', html)
    urls = []

    for h in hrefs:
        u = decode_duckduckgo_href(h)
        if not u:
            continue
        if is_bad_url(u):
            continue
        if u not in urls:
            urls.append(u)
        if len(urls) >= MAX_SEARCH_RESULTS:
            break

    return urls


def fetch_page(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return "", r.url, f"http_{r.status_code}"
        return r.text, r.url, "ok"
    except Exception as e:
        return "", url, f"request_failed: {e}"


def page_relevance(author, institution, html, url):
    text = re.sub(r"\s+", " ", str(html or "")).lower()
    author_norm = norm_name(author)
    first, _, last = name_parts(author)

    score = 0

    if first and first in text:
        score += 2
    if last and last in text:
        score += 4
    if author_norm and author_norm in text:
        score += 8

    institution_words = [
        w.lower() for w in re.split(r"[^A-Za-z0-9]+", institution)
        if len(w) >= 4 and w.lower() not in {"department", "division", "school", "medicine", "medical", "university", "hospital"}
    ]

    for w in institution_words[:5]:
        if w in text:
            score += 2

    url_l = str(url).lower()
    if any(token in url_l for token in ["faculty", "profile", "people", "directory", "physician", "doctor", "staff"]):
        score += 3

    return score


def build_queries(author, institution):
    base = f'"{author}"'
    queries = []

    if institution:
        queries.extend([
            f'{base} "{institution}" email',
            f'{base} "{institution}" faculty profile',
            f'{base} "{institution}" physician',
        ])

    queries.extend([
        f'{base} email faculty',
        f'{base} physician profile email',
    ])

    return queries


def write_empty():
    cols = [
        "Author", "Institution", "Email Candidates", "Best Faculty Email",
        "Email Match Score", "Page Relevance Score", "Faculty Page URL", "Search Query", "Evidence"
    ]
    pd.DataFrame(columns=cols).to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")


def main():
    if not AUTHOR_FILE.exists():
        print(f"Missing author file: {AUTHOR_FILE}")
        write_empty()
        return

    try:
        authors = pd.read_csv(AUTHOR_FILE)
    except Exception as e:
        print(f"Could not read author file: {e}")
        write_empty()
        return

    if authors.empty or "Author" not in authors.columns:
        print("No authors to search.")
        write_empty()
        return

    if "Strategic Fit Score" in authors.columns:
        authors = authors.sort_values("Strategic Fit Score", ascending=False)
    elif "Author_Expertise_Score" in authors.columns:
        authors = authors.sort_values("Author_Expertise_Score", ascending=False)

    authors = authors.head(MAX_AUTHORS)

    rows = []
    seen_author_email = set()

    for idx, row in authors.iterrows():
        author = clean(row.get("Author", ""))
        institution = clean(row.get("Institution", ""))

        if not author:
            continue

        print(f"Faculty email search {len(rows)+1}: {author}")

        queries = build_queries(author, institution)
        checked_urls = set()
        found_for_author = []

        for query in queries:
            urls = search_web(query)
            time.sleep(REQUEST_DELAY)

            for url in urls:
                if url in checked_urls:
                    continue
                checked_urls.add(url)

                if len(checked_urls) > MAX_PAGES_PER_AUTHOR:
                    break

                html, resolved_url, status = fetch_page(url)
                time.sleep(REQUEST_DELAY)

                if status != "ok" or not html:
                    continue

                relevance = page_relevance(author, institution, html, resolved_url)
                if relevance < 6:
                    continue

                emails = extract_emails(html)
                if not emails:
                    continue

                scored_emails = []
                for email in emails:
                    match_score = email_name_match_score(author, email)
                    if match_score >= 18:
                        scored_emails.append((email, match_score))

                if not scored_emails:
                    continue

                scored_emails = sorted(scored_emails, key=lambda x: x[1], reverse=True)
                best_email, best_score = scored_emails[0]
                all_emails = "; ".join(e for e, _ in scored_emails)

                key = (author, best_email)
                if key in seen_author_email:
                    continue
                seen_author_email.add(key)

                found_for_author.append(best_email)

                rows.append({
                    "Author": author,
                    "Institution": institution,
                    "Email Candidates": all_emails,
                    "Best Faculty Email": best_email,
                    "Email Match Score": best_score,
                    "Page Relevance Score": relevance,
                    "Faculty Page URL": resolved_url,
                    "Search Query": query,
                    "Evidence": f"page_relevance={relevance}; name_match={best_score}",
                })

            if found_for_author:
                break

    cols = [
        "Author", "Institution", "Email Candidates", "Best Faculty Email",
        "Email Match Score", "Page Relevance Score", "Faculty Page URL", "Search Query", "Evidence"
    ]

    out = pd.DataFrame(rows)
    if out.empty:
        out = pd.DataFrame(columns=cols)
    else:
        out = out[cols]

    out.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"Saved {OUTPUT_FILE}")
    print(f"Faculty emails found: {len(out)}")


if __name__ == "__main__":
    main()
