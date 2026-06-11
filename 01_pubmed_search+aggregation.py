from Bio import Entrez
import pandas as pd
import re

from config import (
    SEARCH_TERMS,
    RELEVANCE_TERMS,
    START_DATE,
    END_DATE,
    RETMAX,
    MIN_ARTICLE_SCORE,
    AUTHOR_WEIGHTS,
    NCBI_EMAIL,
    PAPERS_SCORED_FILE,
    AUTHOR_AGG_FILE,
)

Entrez.email = NCBI_EMAIL


def pubmed_term(term):
    term = str(term).strip()
    if not term:
        return ""
    if term.endswith("*"):
        return f"{term}[Title/Abstract]"
    return f'"{term}"[Title/Abstract]'


def build_search_query():
    disease_query = " OR ".join(
        pubmed_term(term) for term in SEARCH_TERMS if str(term).strip()
    )
    relevance_query = " OR ".join(
        pubmed_term(term) for term in RELEVANCE_TERMS.keys() if str(term).strip()
    )

    return f"""
(
    {disease_query}
)
AND
(
    {relevance_query}
)
AND
(
    "{START_DATE}"[Date - Publication] : "{END_DATE}"[Date - Publication]
)
"""


def score_article(title, abstract):
    text_title = str(title).lower()
    text_abstract = str(abstract).lower()

    score = 0
    matched_keywords = []

    for keyword, points in RELEVANCE_TERMS.items():
        keyword_lower = str(keyword).lower()

        if keyword_lower in text_title:
            score += points * 3
            matched_keywords.append(f"{keyword} (title)")

        if keyword_lower in text_abstract:
            score += points
            matched_keywords.append(f"{keyword} (abstract)")

    return score, "; ".join(matched_keywords)


def get_author_weight(index, total_authors):
    if total_authors == 1:
        return AUTHOR_WEIGHTS.get("single", 1.0), "single"
    if index == 0:
        return AUTHOR_WEIGHTS.get("first", 0.8), "first"
    if index == total_authors - 1:
        return AUTHOR_WEIGHTS.get("last", 0.9), "last"
    return AUTHOR_WEIGHTS.get("middle", 0.2), "middle"


def safe_get_abstract(article_info):
    try:
        abstract_list = article_info["Abstract"]["AbstractText"]
        return " ".join(str(x) for x in abstract_list)
    except Exception:
        return ""


def safe_get_journal(article_info):
    try:
        return str(article_info["Journal"]["Title"])
    except Exception:
        return "Unknown"


def safe_get_year(article_info):
    try:
        pub_date = article_info["Journal"]["JournalIssue"]["PubDate"]
        if "Year" in pub_date:
            return str(pub_date["Year"])
        if "MedlineDate" in pub_date:
            match = re.search(r"(19|20)\d{2}", str(pub_date["MedlineDate"]))
            return match.group(0) if match else "Unknown"
    except Exception:
        pass
    return "Unknown"


def safe_get_doi(article_info):
    try:
        for item in article_info.get("ELocationID", []):
            if str(item.attributes.get("EIdType", "")).lower() == "doi":
                return str(item)
    except Exception:
        pass
    return ""


def get_author_name(author):
    last_name = author.get("LastName", "")
    fore_name = author.get("ForeName", "")
    collective_name = author.get("CollectiveName", "")
    full_name = f"{fore_name} {last_name}".strip()
    return full_name or collective_name


def get_author_affiliation(author):
    affiliations = []
    try:
        for aff in author.get("AffiliationInfo", []):
            aff_text = str(aff.get("Affiliation", "")).strip()
            if aff_text:
                affiliations.append(aff_text)
    except Exception:
        pass
    return " || ".join(dict.fromkeys(affiliations))


def parse_department(affiliation):
    if not affiliation:
        return ""
    parts = [p.strip() for p in re.split(r",|;", str(affiliation)) if p.strip()]
    for p in parts:
        if re.search(r"\b(department|division|section|center|centre|institute|clinic|program)\b", p, re.I):
            return p
    return ""


def parse_institution(affiliation):
    if not affiliation:
        return ""

    parts = [p.strip() for p in re.split(r",|;", str(affiliation)) if p.strip()]
    institution_patterns = [
        r"\buniversity\b", r"\bhospital\b", r"\bclinic\b",
        r"\bmedical school\b", r"\bschool of medicine\b",
        r"\binstitute\b", r"\bcenter\b", r"\bcentre\b",
        r"\bcollege\b", r"\bhealth\b", r"\bmedical center\b",
    ]

    for p in parts:
        if any(re.search(pattern, p, re.I) for pattern in institution_patterns):
            return p

    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else ""


def write_empty_outputs():
    paper_cols = [
        "PMID", "Title", "Abstract", "Authors", "Journal", "Year",
        "DOI", "PubMed_Link", "DOI_Link", "Relevance Score", "Matched Keywords"
    ]
    author_cols = [
        "Author", "Author_Expertise_Score", "Relevant_Paper_Count",
        "Average_Article_Score", "Max_Article_Score", "Years",
        "Representative_PMIDs", "Representative_Article_Titles",
        "Representative_PubMed_Links", "Representative_DOIs",
        "Example_Titles", "Journals", "Institution", "Department",
        "Affiliation", "Matched_Keywords"
    ]
    pd.DataFrame(columns=paper_cols).to_csv(PAPERS_SCORED_FILE, index=False)
    pd.DataFrame(columns=author_cols).to_csv(AUTHOR_AGG_FILE, index=False)


def join_top_unique(series, n=5, sep="; "):
    vals = []
    for v in series:
        v = str(v).strip()
        if v and v.lower() not in ["nan", "unknown"] and v not in vals:
            vals.append(v)
        if len(vals) >= n:
            break
    return sep.join(vals)


def main():
    if not SEARCH_TERMS or not RELEVANCE_TERMS:
        print("No search or relevance terms found.")
        write_empty_outputs()
        return

    search_term = build_search_query()

    print("Searching PubMed with query:")
    print(search_term)

    search = Entrez.esearch(
        db="pubmed",
        term=search_term,
        retmax=RETMAX,
        sort="relevance",
    )

    search_results = Entrez.read(search)
    id_list = search_results["IdList"]

    print(f"Found {len(id_list)} papers")

    if not id_list:
        write_empty_outputs()
        return

    fetch = Entrez.efetch(
        db="pubmed",
        id=",".join(id_list),
        rettype="abstract",
        retmode="xml",
    )

    papers = Entrez.read(fetch)

    paper_results = []
    author_rows = []

    for article in papers["PubmedArticle"]:
        citation = article["MedlineCitation"]
        article_info = citation["Article"]

        pmid = str(citation["PMID"])
        title = str(article_info.get("ArticleTitle", ""))
        abstract = safe_get_abstract(article_info)
        journal = safe_get_journal(article_info)
        year = safe_get_year(article_info)
        doi = safe_get_doi(article_info)

        pubmed_link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
        doi_link = f"https://doi.org/{doi}" if doi else ""

        relevance_score, matched_keywords = score_article(title, abstract)
        authors_clean = []

        try:
            authors_list = article_info["AuthorList"]
        except Exception:
            authors_list = []

        for i, author in enumerate(authors_list):
            full_name = get_author_name(author)
            if not full_name:
                continue

            affiliation = get_author_affiliation(author)
            department = parse_department(affiliation)
            institution = parse_institution(affiliation)

            weight, position = get_author_weight(i, len(authors_list))
            weighted_score = relevance_score * weight

            authors_clean.append(full_name)

            author_rows.append({
                "Author": full_name,
                "PMID": pmid,
                "Title": title,
                "Year": year,
                "Journal": journal,
                "DOI": doi,
                "PubMed_Link": pubmed_link,
                "DOI_Link": doi_link,
                "Affiliation": affiliation,
                "Department": department,
                "Institution": institution,
                "Article Relevance Score": relevance_score,
                "Author Position": position,
                "Position Weight": weight,
                "Weighted Author Score": weighted_score,
                "Matched Keywords": matched_keywords,
            })

        paper_results.append({
            "PMID": pmid,
            "Title": title,
            "Abstract": abstract,
            "Authors": "; ".join(authors_clean),
            "Journal": journal,
            "Year": year,
            "DOI": doi,
            "PubMed_Link": pubmed_link,
            "DOI_Link": doi_link,
            "Relevance Score": relevance_score,
            "Matched Keywords": matched_keywords,
        })

    paper_df = pd.DataFrame(paper_results)
    if not paper_df.empty:
        paper_df = paper_df.sort_values(by="Relevance Score", ascending=False)
    paper_df.to_csv(PAPERS_SCORED_FILE, index=False)

    author_df = pd.DataFrame(author_rows)
    if author_df.empty:
        write_empty_outputs()
        return

    filtered_author_df = author_df[
        author_df["Article Relevance Score"] >= MIN_ARTICLE_SCORE
    ]

    if filtered_author_df.empty:
        write_empty_outputs()
        return

    author_agg = (
        filtered_author_df
        .sort_values(by="Article Relevance Score", ascending=False)
        .groupby("Author")
        .agg(
            Author_Expertise_Score=("Weighted Author Score", "sum"),
            Relevant_Paper_Count=("PMID", "count"),
            Average_Article_Score=("Article Relevance Score", "mean"),
            Max_Article_Score=("Article Relevance Score", "max"),
            Years=("Year", lambda x: "; ".join(sorted(set(str(y) for y in x if str(y) != "Unknown")))),
            Representative_PMIDs=("PMID", lambda x: join_top_unique(x, 5)),
            Representative_Article_Titles=("Title", lambda x: join_top_unique(x, 5, " || ")),
            Representative_PubMed_Links=("PubMed_Link", lambda x: join_top_unique(x, 5)),
            Representative_DOIs=("DOI_Link", lambda x: join_top_unique(x, 5)),
            Example_Titles=("Title", lambda x: " || ".join(list(x)[:3])),
            Journals=("Journal", lambda x: "; ".join(sorted(set(str(j) for j in x))[:5])),
            Institution=("Institution", lambda x: join_top_unique(x, 3)),
            Department=("Department", lambda x: join_top_unique(x, 3)),
            Affiliation=("Affiliation", lambda x: join_top_unique(x, 3, " || ")),
            Matched_Keywords=("Matched Keywords", lambda x: join_top_unique(x, 5)),
        )
        .reset_index()
    )

    author_agg = author_agg.sort_values(
        by="Author_Expertise_Score",
        ascending=False,
    )

    author_agg.to_csv(AUTHOR_AGG_FILE, index=False)

    print("Saved:")
    print(f"1. {PAPERS_SCORED_FILE}")
    print(f"2. {AUTHOR_AGG_FILE}")
    print()
    print(author_agg.head(20))


if __name__ == "__main__":
    main()
