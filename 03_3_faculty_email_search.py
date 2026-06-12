import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

import pandas as pd
import requests

from config import AUTHOR_AGG_V2_FILE, PAPERS_SCORED_FILE

AUTHOR_FILE = Path(AUTHOR_AGG_V2_FILE)
RUN_DIR = Path(PAPERS_SCORED_FILE).parent
OUTPUT_FILE = RUN_DIR / "faculty_email_candidates.csv"

# Change to 100 for faster runs or 200 for deeper search.
MAX_AUTHORS = 150
MAX_SEARCH_RESULTS = 6
MAX_PAGES_PER_AUTHOR = 4
REQUEST_TIMEOUT = 7
REQUEST_DELAY = 0.25

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_EMAIL_PARTS = [
    "noreply", "no-reply", "example", "support", "webmaster", "privacy",
    "admin@", "info@", "newsletter", "media@", "press@", "careers@", "jobs@",
    "help@", "editorial", "permissions", "rights", "advertising", "billing",
]
LOW_VALUE_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "instagram.com",
    "youtube.com", "wikipedia.org", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov",
    "researchgate.net", "semanticscholar.org", "scopus.com", "orcid.org",
}
HEADERS = {"User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def clean(x):
    return "" if pd.isna(x) else str(x).strip()


def norm_name(name):
    name = re.sub(r"[^A-Za-z\s\-]", " ", clean(name))
    return re.sub(r"\s+", " ", name).strip().lower()


def name_parts(name):
    parts = norm_name(name).split()
    if not parts:
        return "", "", ""
    return parts[0], "".join(parts[1:-1]), parts[-1]


def email_name_match_score(author, email):
    first, _, last = name_parts(author)
    if not first or not last or "@" not in email:
        return 0
    local = email.split("@")[0].lower().replace("-", ".").replace("_", ".")
    compact = local.replace(".", "")
    score = 0
    if local == f"{first}.{last}": score += 30
    if local == f"{first[0]}.{last}": score += 28
    if compact == f"{first}{last}": score += 28
    if compact == f"{first[0]}{last}": score += 26
    if last in local: score += 12
    if first in local: score += 8
    if compact.startswith(first[0] + last[:4]): score += 8
    return score


def extract_emails(text):
    out = []
    for e in EMAIL_RE.findall(str(text or "")):
        e = e.strip().strip(".,;:()[]{}<>").lower()
        if any(bad in e for bad in BAD_EMAIL_PARTS):
            continue
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf")):
            continue
        if e not in out:
            out.append(e)
    return out


def is_bad_url(url):
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        return True
    return (not domain) or any(bad in domain for bad in LOW_VALUE_DOMAINS)


def decode_ddg_href(href):
    href = (href or "").replace("&amp;", "&")
    if href.startswith("//"):
        href = "https:" + href
    if "uddg=" in href:
        try:
            qs = parse_qs(urlparse(href).query)
            return unquote(qs.get("uddg", [""])[0])
        except Exception:
            return ""
    return href if href.startswith("http") else ""


def search_duckduckgo(query):
    try:
        r = requests.get("https://html.duckduckgo.com/html/", params={"q": query}, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code >= 400:
            return []
        hrefs = re.findall(r'href="([^"]+)"', r.text)
    except Exception:
        return []
    urls = []
    for h in hrefs:
        u = decode_ddg_href(h)
        if u and not is_bad_url(u) and u not in urls:
            urls.append(u)
        if len(urls) >= MAX_SEARCH_RESULTS:
            break
    return urls


def search_bing(query):
    try:
        r = requests.get("https://www.bing.com/search", params={"q": query}, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code >= 400:
            return []
        hrefs = re.findall(r'<a href="(https?://[^"]+)"', r.text)
    except Exception:
        return []
    urls = []
    for u in hrefs:
        u = u.replace("&amp;", "&")
        if not is_bad_url(u) and u not in urls:
            urls.append(u)
        if len(urls) >= MAX_SEARCH_RESULTS:
            break
    return urls


def search_web(query):
    urls = search_duckduckgo(query)
    if len(urls) < 2:
        for u in search_bing(query):
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
    if first and first in text: score += 2
    if last and last in text: score += 4
    if author_norm and author_norm in text: score += 8
    ignore = {"department", "division", "school", "medicine", "medical", "university", "hospital", "health", "center", "centre"}
    words = [w.lower() for w in re.split(r"[^A-Za-z0-9]+", institution) if len(w) >= 4 and w.lower() not in ignore]
    for w in words[:6]:
        if w in text: score += 2
    if any(t in str(url).lower() for t in ["faculty", "profile", "people", "directory", "physician", "doctor", "staff", "provider"]):
        score += 3
    return score


def build_queries(author, institution):
    base = f'"{author}"'
    q = []
    if institution:
        q += [
            f'{base} "{institution}" email',
            f'{base} "{institution}" faculty profile',
            f'{base} "{institution}" physician profile',
            f'{base} "{institution}" staff directory',
        ]
    q += [f'{base} email faculty', f'{base} physician profile email']
    return list(dict.fromkeys(q))


def write_empty():
    cols = ["Author", "Institution", "Email Candidates", "Best Faculty Email", "Email Match Score", "Page Relevance Score", "Faculty Page URL", "Search Query", "Evidence"]
    pd.DataFrame(columns=cols).to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")


def main():
    if not AUTHOR_FILE.exists():
        write_empty(); return
    authors = pd.read_csv(AUTHOR_FILE)
    if authors.empty or "Author" not in authors.columns:
        write_empty(); return
    if "Strategic Fit Score" in authors.columns:
        authors = authors.sort_values("Strategic Fit Score", ascending=False)
    elif "Author_Expertise_Score" in authors.columns:
        authors = authors.sort_values("Author_Expertise_Score", ascending=False)
    authors = authors.head(MAX_AUTHORS)
    rows, seen = [], set()
    for count, (_, row) in enumerate(authors.iterrows(), start=1):
        author, institution = clean(row.get("Author", "")), clean(row.get("Institution", ""))
        if not author: continue
        print(f"Faculty email search {count}/{len(authors)}: {author}")
        found = False
        checked = set()
        for query in build_queries(author, institution):
            for url in search_web(query):
                if url in checked: continue
                checked.add(url)
                if len(checked) > MAX_PAGES_PER_AUTHOR: break
                html, resolved_url, status = fetch_page(url)
                time.sleep(REQUEST_DELAY)
                if status != "ok" or not html: continue
                relevance = page_relevance(author, institution, html, resolved_url)
                if relevance < 6: continue
                scored = [(e, email_name_match_score(author, e)) for e in extract_emails(html)]
                scored = [(e, s) for e, s in scored if s >= 18]
                if not scored: continue
                scored.sort(key=lambda x: x[1], reverse=True)
                best_email, best_score = scored[0]
                key = (author, best_email)
                if key in seen: continue
                seen.add(key)
                found = True
                rows.append({
                    "Author": author,
                    "Institution": institution,
                    "Email Candidates": "; ".join(e for e, _ in scored),
                    "Best Faculty Email": best_email,
                    "Email Match Score": best_score,
                    "Page Relevance Score": relevance,
                    "Faculty Page URL": resolved_url,
                    "Search Query": query,
                    "Evidence": f"page_relevance={relevance}; name_match={best_score}",
                })
            if found: break
            time.sleep(REQUEST_DELAY)
    out = pd.DataFrame(rows)
    if out.empty:
        write_empty()
    else:
        out.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"Saved {OUTPUT_FILE}")
    print(f"Faculty emails found: {len(out)}")


if __name__ == "__main__":
    main()
