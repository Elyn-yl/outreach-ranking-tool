import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict, Counter
from pathlib import Path

import pandas as pd
import requests

# =========================
# CONFIG
# =========================
AUTHOR_AGG = "author_aggregation_v2.csv"
IBD_PAPERS = "ibd_papers_scored.csv"  # optional, used to build all-author affiliation database
AFFILIATION_SUMMARY = "author_affiliation_summary.csv"
PUBMED_SUMMARY = "author_email_summary.csv"             # from 03_pubmed_email_extractor.py
PUBMED_CANDIDATES = "author_email_candidates.csv"       # from 03_pubmed_email_extractor.py
MULTISOURCE_XLSX = ""

MASTER_CSV = "author_aggregation_master.csv"
FINAL_XLSX = "outreach_rankings.xlsx"

NCBI_EMAIL = "elynyu21@gmail.com"
BATCH_SIZE = 100
DELAY = 0.35

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_EMAIL_PARTS = [
    "noreply", "no-reply", "example", "support", "webmaster", "privacy",
    "admin@", "info@", "newsletter", "media@", "press@", "careers@", "jobs@", "help@"
]
GENERIC_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com"}

# =========================
# BASIC HELPERS
# =========================
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

def domain_family(domain):
    d = clean(domain).lower()
    if not d:
        return ""
    rules = [
        (("bidmc.harvard.edu", "hms.harvard.edu", ".harvard.edu", "harvard.edu"), "harvard_bidmc"),
        (("kcl.ac.uk", ".kcl.ac.uk"), "kcl"),
        (("ucsd.edu", ".ucsd.edu"), "ucsd"),
        (("emory.edu", ".emory.edu", "choa.org"), "emory_choa"),
        (("stanford.edu", ".stanford.edu"), "stanford"),
        (("uhhospitals.org",), "uhhospitals"),
        (("memorialcare.org",), "memorialcare"),
        (("mountsinai.org", "mssm.edu"), "mount_sinai"),
        (("mayo.edu",), "mayo"),
        (("ccf.org",), "cleveland_clinic"),
        (("cshs.org",), "cedars_sinai"),
        (("northwestern.edu",), "northwestern"),
        (("umich.edu",), "umich"),
        (("upenn.edu", "pennmedicine.upenn.edu"), "upenn"),
        (("chop.edu",), "chop"),
        (("cchmc.org",), "cchmc"),
        (("uchicago.edu",), "uchicago"),
    ]
    for needles, key in rules:
        if any(d == n or d.endswith(n) or n in d for n in needles):
            return key
    return d

def institution_keys_from_text(text):
    """Return multiple possible institution/network keys. Never force only one."""
    t = norm_ascii(text).lower()
    keys = set()

    rules = [
        (["bidmc", "beth israel", "harvard medical school", "bidmc.harvard.edu", "hms.harvard.edu"], "harvard_bidmc"),
        (["king s college", "kings college", "king's college", "kcl.ac.uk"], "kcl"),
        (["ucsd", "university of california san diego", "uc san diego", "ucsd.edu"], "ucsd"),
        (["emory", "choa", "children s healthcare of atlanta", "children's healthcare of atlanta", "emory.edu"], "emory_choa"),
        (["stanford", "stanford.edu"], "stanford"),
        (["uhhospitals", "university hospitals", "rainbow babies", "uhhospitals.org"], "uhhospitals"),
        (["memorialcare", "miller children", "memorialcare.org"], "memorialcare"),
        (["mount sinai", "mssm", "mountsinai.org", "mssm.edu"], "mount_sinai"),
        (["mayo clinic", "mayo.edu"], "mayo"),
        (["cleveland clinic", "ccf.org"], "cleveland_clinic"),
        (["cedars sinai", "cedars-sinai", "cshs.org"], "cedars_sinai"),
        (["northwestern", "northwestern.edu"], "northwestern"),
        (["university of michigan", "umich.edu"], "umich"),
        (["university of pennsylvania", "penn medicine", "upenn.edu"], "upenn"),
        (["children s hospital of philadelphia", "children's hospital of philadelphia", "chop.edu"], "chop"),
        (["cincinnati children", "cchmc.org"], "cchmc"),
        (["university of chicago", "uchicago.edu"], "uchicago"),
    ]
    for needles, key in rules:
        if any(n in t for n in needles):
            keys.add(key)

    for e in extract_emails(t):
        fam = domain_family(email_domain(e))
        if fam:
            keys.add(fam)

    # fallback key only if no known rule/domain matched
    if not keys:
        simple = re.sub(r"[^a-z0-9 ]", " ", t)
        simple = re.sub(r"\b(department|division|school|faculty|center|centre|of|and|for|the|medicine|gastroenterology|hospital|clinic|university)\b", " ", simple)
        simple = re.sub(r"\s+", " ", simple).strip()
        if simple:
            keys.add(simple[:80])

    return sorted(keys)

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

# =========================
# PATTERN CLASSIFICATION / GENERATION
# =========================
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

    # Flexible prefix patterns, e.g. kpapamic / acheifet.
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

# =========================
# BUILD ALL-AUTHOR AFFILIATION SUMMARY FROM PUBMED PMIDs
# =========================
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
    if not Path(IBD_PAPERS).exists():
        print(f"Warning: {AFFILIATION_SUMMARY} and {IBD_PAPERS} not found. Institution detection will use existing files only.")
        return

    papers = pd.read_csv(IBD_PAPERS)
    if "PMID" not in papers.columns:
        print(f"Warning: {IBD_PAPERS} has no PMID column. Skipping affiliation summary build.")
        return

    pmids = papers["PMID"].dropna().astype(str).drop_duplicates().tolist()
    rows = []
    print(f"Building {AFFILIATION_SUMMARY} from {len(pmids)} PMIDs...")

    for start in range(0, len(pmids), BATCH_SIZE):
        batch = pmids[start:start+BATCH_SIZE]
        print(f"  PubMed fetch {start+1}-{start+len(batch)}")
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
        print(f"Saved {AFFILIATION_SUMMARY}")

# =========================
# LOAD SOURCE RECORDS
# =========================
def add_record(records_exact, records_sig, source, row):
    author = clean(row.get("Author", ""))
    if not author:
        return
    row_text = " || ".join(clean(v) for v in row.values)
    emails = extract_emails(row_text)
    exact = norm_name(author)
    sig = name_signature(author)
    for records in (records_exact, records_sig):
        key = exact if records is records_exact else sig
        records[key]["texts"].append(row_text)
        for e in emails:
            records[key]["emails"].append(e)
            records[key]["evidence"].append(f"{source}: {e}")

def load_sources():
    records_exact = defaultdict(lambda: {"emails": [], "texts": [], "evidence": []})
    records_sig = defaultdict(lambda: {"emails": [], "texts": [], "evidence": []})

    sources = []
    for label, path, kind in [
        ("affiliation_summary", AFFILIATION_SUMMARY, "csv"),
        ("pubmed_summary", PUBMED_SUMMARY, "csv"),
        ("pubmed_candidates", PUBMED_CANDIDATES, "csv"),
    ]:
        if not Path(path).exists():
            continue
        try:
            sdf = pd.read_excel(path) if kind == "xlsx" else pd.read_csv(path)
            sources.append((label, sdf))
        except Exception as e:
            print(f"Warning: could not read {path}: {e}")

    for label, sdf in sources:
        if "Author" not in sdf.columns:
            continue
        for _, row in sdf.iterrows():
            add_record(records_exact, records_sig, label, row)

    return records_exact, records_sig

# =========================
# MAIN
# =========================
def main():
    build_affiliation_summary_if_possible()

    df = pd.read_csv(AUTHOR_AGG)
    records_exact, records_sig = load_sources()

    emails = []
    email_evidence = []
    institution_keys = []
    institution_source = []

    for _, row in df.iterrows():
        author = clean(row.get("Author", ""))
        exact = norm_name(author)
        sig = name_signature(author)

        texts = [" || ".join(clean(v) for v in row.values)]
        all_emails = extract_emails(texts[0])
        ev = []

        for rec in [records_exact.get(exact, {}), records_sig.get(sig, {})]:
            texts.extend(rec.get("texts", []))
            all_emails.extend(rec.get("emails", []))
            ev.extend(rec.get("evidence", []))

        best = choose_best_email(author, all_emails)
        emails.append(best)
        evidence_line = []
        if best:
            evidence_line.append(f"direct_or_source_text: {best}")
        evidence_line.extend(list(dict.fromkeys(ev))[:6])
        email_evidence.append(" || ".join(evidence_line))

        combined = " || ".join(texts)
        keys = institution_keys_from_text(combined)
        institution_keys.append(";".join(keys))
        institution_source.append(combined[:800])

    df["Email"] = emails
    df["Email_Evidence"] = email_evidence
    df["Institution_Keys"] = institution_keys
    df["Institution_Source_Combined"] = institution_source

    # Build pattern DB by every institution/domain family key.
    pattern_db = defaultdict(list)
    for _, row in df.iterrows():
        author = clean(row.get("Author", ""))
        email = clean(row.get("Email", ""))
        if not author or not email:
            continue
        pattern = classify_pattern(author, email)
        domain = email_domain(email)
        fam = domain_family(domain)
        if not domain or pattern == "unknown":
            continue
        rec = {"author": author, "email": email, "domain": domain, "domain_family": fam, "pattern": pattern}
        keys = [k for k in clean(row.get("Institution_Keys", "")).split(";") if k]
        if fam:
            keys.append(fam)
        for k in sorted(set(keys)):
            pattern_db[k].append(rec)

    inferred = []
    inferred_pat = []
    inferred_conf = []
    inferred_ev = []

    for _, row in df.iterrows():
        author = clean(row.get("Author", ""))
        if clean(row.get("Email", "")):
            inferred.append("")
            inferred_pat.append("")
            inferred_conf.append("confirmed_email_exists")
            inferred_ev.append("")
            continue

        keys = [k for k in clean(row.get("Institution_Keys", "")).split(";") if k]
        candidate_groups = []
        for k in keys:
            ex = pattern_db.get(k, [])
            if ex:
                candidate_groups.append((k, ex))

        if not candidate_groups:
            inferred.append("")
            inferred_pat.append("")
            inferred_conf.append("no_institution_or_domain_pattern")
            inferred_ev.append("")
            continue

        # Choose group with most examples and strongest pattern consistency.
        best_choice = None
        for key, examples in candidate_groups:
            pattern_counts = Counter(e["pattern"] for e in examples)
            domain_counts = Counter(e["domain"] for e in examples)
            best_pattern, pattern_n = pattern_counts.most_common(1)[0]
            best_domain, domain_n = domain_counts.most_common(1)[0]
            total = len(examples)
            consistency = pattern_n / total if total else 0
            score = (pattern_n, domain_n, consistency, total)
            if best_choice is None or score > best_choice[0]:
                best_choice = (score, key, examples, best_pattern, best_domain, pattern_n, domain_n, total, consistency)

        _, matched_key, examples, best_pattern, best_domain, pattern_n, domain_n, total, consistency = best_choice
        email = generate_email(author, best_domain, best_pattern)

        if not email:
            inferred.append("")
            inferred_pat.append("")
            inferred_conf.append("pattern_not_generatable")
            inferred_ev.append("")
            continue

        if total >= 3 and pattern_n >= 2 and domain_n >= 2 and consistency >= 0.60:
            conf = "high"
        elif total >= 2 and pattern_n >= 1 and domain_n >= 1 and consistency >= 0.50:
            conf = "medium"
        else:
            conf = "low"

        ev = "; ".join(f'{e["author"]}->{e["email"]}({e["pattern"]})' for e in examples[:8])
        inferred.append(email)
        inferred_pat.append(f"{best_pattern}@{best_domain}")
        inferred_conf.append(f"{conf}; matched_key={matched_key}; examples={total}; consistency={consistency:.2f}")
        inferred_ev.append(ev)

    df["Inferred_Email"] = inferred
    df["Inferred_Email_Pattern"] = inferred_pat
    df["Inferred_Email_Confidence"] = inferred_conf
    df["Inferred_Email_Evidence"] = inferred_ev

    # Add preferred email for actual outreach.
    # Confirmed PubMed-affiliation emails are safe to use directly.
    # Inferred emails are only promoted to Best Email when confidence is high;
    # medium/low confidence inferred emails are kept separately for manual review.
    preferred_emails = []
    preferred_statuses = []
    review_emails = []

    for _, r in df.iterrows():
        confirmed = clean(r.get("Email", ""))
        inferred_email = clean(r.get("Inferred_Email", ""))
        inferred_conf = clean(r.get("Inferred_Email_Confidence", "")).lower()

        if "@" in confirmed:
            preferred_emails.append(confirmed)
            preferred_statuses.append("confirmed_from_pubmed_affiliation")
            review_emails.append("")
        elif "@" in inferred_email and inferred_conf.startswith("high"):
            preferred_emails.append(inferred_email)
            preferred_statuses.append("inferred_high_confidence")
            review_emails.append("")
        elif "@" in inferred_email:
            preferred_emails.append("")
            preferred_statuses.append("inferred_needs_review")
            review_emails.append(inferred_email)
        else:
            preferred_emails.append("")
            preferred_statuses.append("missing")
            review_emails.append("")

    df["Preferred_Email"] = preferred_emails
    df["Preferred_Email_Status"] = preferred_statuses
    df["Email_To_Review"] = review_emails

    # 1) Full working file for internal review/debugging
    df.to_csv(MASTER_CSV, index=False, encoding="utf-8-sig")

    # 2) Clean final deliverable for Jack
    keep_cols = [
        "Author",
        "Institution",
        "Department",
        "Preferred_Email",
        "Preferred_Email_Status",
        "Email",
        "Inferred_Email",
        "Inferred_Email_Confidence",
        "Author_Expertise_Score",
        "Relevant_Paper_Count",
        "Average_Article_Score",
        "Max_Article_Score",
        "ImmPro Interest Score",
        "Strategic Fit Score",
        "Example_Titles",
        "Interest Signals",
    ]
    final = df[[c for c in keep_cols if c in df.columns]].copy()

    final = final.rename(columns={
        "Author": "Author Name",
        "Institution": "Institution / School",
        "Affiliation": "Raw Affiliation",
        "Preferred_Email": "Best Email",
        "Preferred_Email_Status": "Email Status",
        "Email_To_Review": "Email To Review",
        "Email": "Confirmed Email",
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

    # 2) Clean final deliverable for Jack: three ranking sheets
    with pd.ExcelWriter(FINAL_XLSX, engine="openpyxl") as writer:
        if "Author Expertise Score" in final.columns:
            final.sort_values(
                by="Author Expertise Score",
                ascending=False
            ).to_excel(
                writer,
                sheet_name="Top Experts",
                index=False
            )

        if "Outreach Signal Score" in final.columns:
            final.sort_values(
                by="Outreach Signal Score",
                ascending=False
            ).to_excel(
                writer,
                sheet_name="Top Outreach Fits",
                index=False
            )

        if "Strategic Fit Score" in final.columns:
            final.sort_values(
                by="Strategic Fit Score",
                ascending=False
            ).to_excel(
                writer,
                sheet_name="Balanced Ranking",
                index=False
            )
        else:
            final.to_excel(
                writer,
                sheet_name="Balanced Ranking",
                index=False
            )

        if Path(IBD_PAPERS).exists():
            papers = pd.read_csv(IBD_PAPERS)
            article_cols = [
                "PMID", "Title", "Journal", "Year", "DOI", "PubMed_Link", "DOI_Link",
                "Authors", "Relevance Score", "Matched Keywords"
            ]
            papers[[c for c in article_cols if c in papers.columns]].to_excel(
                writer,
                sheet_name="Article Details",
                index=False,
            )

    print("DONE")
    print(f"Saved working file: {MASTER_CSV}")
    print(f"Saved final deliverable with 3 sheets: {FINAL_XLSX}")
    print(f"Confirmed emails: {df['Email'].astype(str).str.contains('@').sum()}")
    print(f"Inferred emails: {df['Inferred_Email'].astype(str).str.contains('@').sum()}")

if __name__ == "__main__":
    main()
