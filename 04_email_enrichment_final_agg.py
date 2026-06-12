import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import pandas as pd
import requests

from config import (
    NCBI_EMAIL, PAPERS_SCORED_FILE, AUTHOR_AGG_V2_FILE,
    AUTHOR_EMAIL_SUMMARY_FILE, AUTHOR_EMAIL_CANDIDATES_FILE,
    AUTHOR_AFFILIATION_SUMMARY_FILE, AUTHOR_MASTER_FILE, FINAL_XLSX,
)

AUTHOR_AGG = AUTHOR_AGG_V2_FILE
PAPERS_FILE = PAPERS_SCORED_FILE
RUN_DIR = Path(PAPERS_FILE).parent
AFFILIATION_SUMMARY = AUTHOR_AFFILIATION_SUMMARY_FILE
PUBMED_SUMMARY = AUTHOR_EMAIL_SUMMARY_FILE
PUBMED_CANDIDATES = AUTHOR_EMAIL_CANDIDATES_FILE
FACULTY_EMAIL_CANDIDATES = RUN_DIR / "faculty_email_candidates.csv"
MASTER_CSV = AUTHOR_MASTER_FILE
BATCH_SIZE = 100
DELAY = 0.35
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_EMAIL_PARTS = ["noreply", "no-reply", "example", "support", "webmaster", "privacy", "admin@", "info@", "newsletter", "media@", "press@", "careers@", "jobs@", "help@", "editorial", "permissions", "rights", "advertising", "billing"]
LOW_TRUST_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "qq.com", "126.com", "163.com", "sina.com", "yeah.net"}


def clean(x): return "" if pd.isna(x) else str(x).strip()

def norm_ascii(text):
    text = unicodedata.normalize("NFKD", clean(text))
    return "".join(c for c in text if not unicodedata.combining(c))

def norm_name(name):
    name = re.sub(r"[^A-Za-z\s\-]", " ", norm_ascii(name))
    return re.sub(r"\s+", " ", name).strip().lower()

def name_parts(name):
    parts = norm_name(name).split()
    if not parts: return "", "", ""
    return parts[0], "".join(parts[1:-1]), parts[-1]

def name_signature(name):
    first, _, last = name_parts(name)
    return f"{last}|{first[:1]}" if first and last else norm_name(name)

def extract_emails(text):
    out = []
    for e in EMAIL_RE.findall(clean(text)):
        e = e.strip().strip(".,;:()[]{}<>").lower()
        if any(bad in e for bad in BAD_EMAIL_PARTS): continue
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf")): continue
        if e not in out: out.append(e)
    return out

def email_domain(email): return email.split("@")[-1].lower() if "@" in email else ""

def email_name_match_score(author, email):
    first, _, last = name_parts(author)
    if not first or not last or "@" not in email: return 0
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
    if email_domain(email) not in LOW_TRUST_DOMAINS and score > 0: score += 2
    return score

def choose_best_email(author, emails):
    emails = list(dict.fromkeys([e for e in emails if "@" in e]))
    if not emails: return "", 0
    scored = sorted([(e, email_name_match_score(author, e)) for e in emails], key=lambda x: x[1], reverse=True)
    return scored[0]

def is_safe_best_email(author, email, email_usage):
    if not email or "@" not in email: return False
    if email_usage.get(email, 0) > 1: return False
    return email_name_match_score(author, email) >= 18

def safe_text(el): return "" if el is None else "".join(el.itertext()).strip()

def get_author_name(author_el):
    fore, last, collective = safe_text(author_el.find("ForeName")), safe_text(author_el.find("LastName")), safe_text(author_el.find("CollectiveName"))
    return f"{fore} {last}".strip() or collective

def build_affiliation_summary_if_possible():
    if Path(AFFILIATION_SUMMARY).exists() or not Path(PAPERS_FILE).exists(): return
    papers = pd.read_csv(PAPERS_FILE)
    if "PMID" not in papers.columns or papers.empty: return
    rows, pmids = [], papers["PMID"].dropna().astype(str).drop_duplicates().tolist()
    for start in range(0, len(pmids), BATCH_SIZE):
        batch = pmids[start:start + BATCH_SIZE]
        print(f"  PubMed fetch {start + 1}-{start + len(batch)}")
        try:
            r = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", params={"db": "pubmed", "id": ",".join(batch), "retmode": "xml", "email": NCBI_EMAIL}, timeout=60)
            r.raise_for_status(); root = ET.fromstring(r.text)
        except Exception as e:
            print(f"  Fetch failed: {e}"); continue
        for article in root.findall(".//PubmedArticle"):
            pmid, title = safe_text(article.find(".//PMID")), safe_text(article.find(".//ArticleTitle"))
            for a in article.findall(".//Author"):
                author = get_author_name(a)
                if not author: continue
                affs = [safe_text(aff) for aff in a.findall(".//AffiliationInfo/Affiliation")]
                aff_text = " || ".join([x for x in affs if x])
                rows.append({"Author": author, "PMID": pmid, "Title": title, "Affiliation": aff_text, "Emails_In_Affiliation": "; ".join(extract_emails(aff_text))})
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
    if not author: return
    row_text = " || ".join(clean(v) for v in row.values)
    emails = extract_emails(row_text)
    exact, sig = norm_name(author), name_signature(author)
    for records, key in [(records_exact, exact), (records_sig, sig)]:
        records[key]["texts"].append(row_text)
        for e in emails:
            records[key]["emails"].append(e)
            records[key]["evidence"].append(f"{source}: {e}")

def load_pubmed_sources():
    records_exact = defaultdict(lambda: {"emails": [], "texts": [], "evidence": []})
    records_sig = defaultdict(lambda: {"emails": [], "texts": [], "evidence": []})
    for label, path in [("affiliation_summary", AFFILIATION_SUMMARY), ("pubmed_summary", PUBMED_SUMMARY), ("pubmed_candidates", PUBMED_CANDIDATES)]:
        if not Path(path).exists(): continue
        try: sdf = pd.read_csv(path)
        except Exception as e:
            print(f"Warning: could not read {path}: {e}"); continue
        if "Author" not in sdf.columns: continue
        for _, row in sdf.iterrows(): add_record(records_exact, records_sig, label, row)
    return records_exact, records_sig

def load_faculty_email_candidates():
    if not FACULTY_EMAIL_CANDIDATES.exists(): return {}
    try: df = pd.read_csv(FACULTY_EMAIL_CANDIDATES)
    except Exception as e:
        print(f"Warning: could not read {FACULTY_EMAIL_CANDIDATES}: {e}"); return {}
    if df.empty or "Author" not in df.columns or "Best Faculty Email" not in df.columns: return {}
    out = {}
    for _, row in df.iterrows():
        author, email = clean(row.get("Author", "")), clean(row.get("Best Faculty Email", "")).lower()
        if not author or "@" not in email: continue
        try: score = int(float(row.get("Email Match Score", 0)))
        except Exception: score = email_name_match_score(author, email)
        if score < 18: continue
        cand = {"email": email, "score": score, "url": clean(row.get("Faculty Page URL", "")), "evidence": clean(row.get("Evidence", ""))}
        if author not in out or score > out[author]["score"]: out[author] = cand
    return out

def collect_pubmed_email_candidates(df, records_exact, records_sig):
    candidate_map, evidence_map, email_to_authors = {}, {}, defaultdict(set)
    for _, row in df.iterrows():
        author = clean(row.get("Author", "")); exact, sig = norm_name(author), name_signature(author)
        all_emails, evidence = extract_emails(" || ".join(clean(v) for v in row.values)), []
        for rec in [records_exact.get(exact, {}), records_sig.get(sig, {})]:
            all_emails.extend(rec.get("emails", [])); evidence.extend(rec.get("evidence", []))
        all_emails = list(dict.fromkeys(all_emails))
        candidate_map[author] = all_emails
        evidence_map[author] = " || ".join(list(dict.fromkeys(evidence))[:8])
        for email in all_emails: email_to_authors[email].add(author)
    return candidate_map, evidence_map, {email: len(authors) for email, authors in email_to_authors.items()}

def build_email_columns(df, pubmed_candidate_map, evidence_map, email_usage, faculty_map):
    emails, statuses, scores, evidence_lines = [], [], [], []
    faculty_usage = defaultdict(set)
    for author, data in faculty_map.items(): faculty_usage[data["email"]].add(author)
    faculty_usage = {email: len(authors) for email, authors in faculty_usage.items()}
    for _, row in df.iterrows():
        author = clean(row.get("Author", "")); faculty = faculty_map.get(author)
        if faculty and faculty_usage.get(faculty["email"], 0) == 1:
            emails.append(faculty["email"]); statuses.append("faculty_page_author_matched_email"); scores.append(faculty["score"])
            evidence_lines.append(f'faculty_page: {faculty["url"]}; {faculty["evidence"]}'); continue
        best_candidate, score = choose_best_email(author, pubmed_candidate_map.get(author, []))
        scores.append(score)
        if is_safe_best_email(author, best_candidate, email_usage):
            emails.append(best_candidate); statuses.append("author_matched_unique_pubmed_affiliation_email"); evidence_lines.append(evidence_map.get(author, ""))
        else:
            emails.append(best_candidate if best_candidate else "")
            if best_candidate:
                if email_usage.get(best_candidate, 0) > 1: statuses.append(f"shared_or_corresponding_email_not_assigned; authors={email_usage.get(best_candidate, 0)}")
                elif score < 18: statuses.append("weak_name_match_not_assigned")
                else: statuses.append("email_not_assigned_needs_review")
            else:
                statuses.append("missing")
            evidence_lines.append(evidence_map.get(author, ""))
    df["Email"], df["Email_Status"], df["Email_Match_Score"], df["Email_Evidence"] = emails, statuses, scores, evidence_lines
    return df

def build_final_columns(df):
    keep_cols = ["Author", "Institution", "Department", "Affiliation", "Email", "Email_Status", "Email_Match_Score", "Email_Evidence", "Author_Expertise_Score", "Relevant_Paper_Count", "Average_Article_Score", "Max_Article_Score", "Years", "ImmPro Interest Score", "Interest Signals", "Strategic Fit Score", "Representative_PMIDs", "Representative_Article_Titles", "Representative_PubMed_Links", "Representative_DOIs", "Journals", "Matched_Keywords", "Faculty Search Query", "Email Search Query", "Program Query", "Clinical Trial Query", "Industry Collaboration Query", "Google Scholar Query", "Manual Notes"]
    final = df[[c for c in keep_cols if c in df.columns]].copy()
    return final.rename(columns={"Author": "Author Name", "Institution": "Institution / School", "Affiliation": "Raw Affiliation", "Email_Status": "Email Status", "Email_Match_Score": "Email Match Score", "Email_Evidence": "Email Evidence", "Author_Expertise_Score": "Author Expertise Score", "Relevant_Paper_Count": "Relevant Paper Count", "Average_Article_Score": "Average Article Score", "Max_Article_Score": "Max Article Score", "ImmPro Interest Score": "Outreach Signal Score", "Representative_PMIDs": "Representative PMIDs", "Representative_Article_Titles": "Representative Article Titles", "Representative_PubMed_Links": "PubMed Article Links", "Representative_DOIs": "DOI Links", "Matched_Keywords": "Matched Relevance Keywords"})

def main():
    build_affiliation_summary_if_possible()
    if not Path(AUTHOR_AGG).exists(): raise FileNotFoundError(f"Missing {AUTHOR_AGG}")
    df = pd.read_csv(AUTHOR_AGG)
    if df.empty:
        df.to_csv(MASTER_CSV, index=False, encoding="utf-8-sig")
        with pd.ExcelWriter(FINAL_XLSX, engine="openpyxl") as writer: pd.DataFrame().to_excel(writer, sheet_name="Balanced Ranking", index=False)
        return
    records_exact, records_sig = load_pubmed_sources()
    pubmed_candidate_map, evidence_map, email_usage = collect_pubmed_email_candidates(df, records_exact, records_sig)
    faculty_map = load_faculty_email_candidates()
    df = build_email_columns(df, pubmed_candidate_map, evidence_map, email_usage, faculty_map)
    final = build_final_columns(df)
    df.to_csv(MASTER_CSV, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(FINAL_XLSX, engine="openpyxl") as writer:
        if "Author Expertise Score" in final.columns: final.sort_values(by="Author Expertise Score", ascending=False).to_excel(writer, sheet_name="Top Experts", index=False)
        if "Outreach Signal Score" in final.columns: final.sort_values(by="Outreach Signal Score", ascending=False).to_excel(writer, sheet_name="Top Outreach Fits", index=False)
        if "Strategic Fit Score" in final.columns: final.sort_values(by="Strategic Fit Score", ascending=False).to_excel(writer, sheet_name="Balanced Ranking", index=False)
        else: final.to_excel(writer, sheet_name="Balanced Ranking", index=False)
        if Path(PAPERS_FILE).exists():
            papers = pd.read_csv(PAPERS_FILE)
            cols = ["PMID", "Title", "Journal", "Year", "DOI", "PubMed_Link", "DOI_Link", "Authors", "Relevance Score", "Matched Keywords"]
            papers[[c for c in cols if c in papers.columns]].to_excel(writer, sheet_name="Article Details", index=False)
        if FACULTY_EMAIL_CANDIDATES.exists(): pd.read_csv(FACULTY_EMAIL_CANDIDATES).to_excel(writer, sheet_name="Faculty Email Candidates", index=False)
    print("DONE")
    print(f"Saved final deliverable: {FINAL_XLSX}")
    print(f"Emails displayed: {final['Email'].astype(str).str.contains('@').sum() if 'Email' in final.columns else 0}")

if __name__ == "__main__": main()
