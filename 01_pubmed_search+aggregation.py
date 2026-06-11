from Bio import Entrez
import pandas as pd

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
        return article_info["Journal"]["JournalIssue"]["PubDate"]["Year"]
    except Exception:
        return "Unknown"


def main():
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
        pd.DataFrame().to_csv(PAPERS_SCORED_FILE, index=False)
        pd.DataFrame().to_csv(AUTHOR_AGG_FILE, index=False)
        print("No papers found.")
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

        relevance_score, matched_keywords = score_article(title, abstract)

        authors_clean = []

        try:
            authors_list = article_info["AuthorList"]

            for i, author in enumerate(authors_list):
                last_name = author.get("LastName", "")
                fore_name = author.get("ForeName", "")
                collective_name = author.get("CollectiveName", "")

                full_name = f"{fore_name} {last_name}".strip() or collective_name

                if not full_name:
                    continue

                weight, position = get_author_weight(i, len(authors_list))
                weighted_score = relevance_score * weight

                authors_clean.append(full_name)

                author_rows.append({
                    "Author": full_name,
                    "PMID": pmid,
                    "Title": title,
                    "Year": year,
                    "Journal": journal,
                    "Article Relevance Score": relevance_score,
                    "Author Position": position,
                    "Position Weight": weight,
                    "Weighted Author Score": weighted_score,
                    "Matched Keywords": matched_keywords,
                })

        except Exception:
            authors_clean = []

        paper_results.append({
            "PMID": pmid,
            "Title": title,
            "Abstract": abstract,
            "Authors": "; ".join(authors_clean),
            "Journal": journal,
            "Year": year,
            "Relevance Score": relevance_score,
            "Matched Keywords": matched_keywords,
        })

    paper_df = pd.DataFrame(paper_results).sort_values(
        by="Relevance Score",
        ascending=False,
    )

    paper_df.to_csv(PAPERS_SCORED_FILE, index=False)

    author_df = pd.DataFrame(author_rows)

    if author_df.empty:
        pd.DataFrame().to_csv(AUTHOR_AGG_FILE, index=False)
        print("No authors found.")
        return

    filtered_author_df = author_df[
        author_df["Article Relevance Score"] >= MIN_ARTICLE_SCORE
    ]

    if filtered_author_df.empty:
        pd.DataFrame(columns=[
            "Author",
            "Author_Expertise_Score",
            "Relevant_Paper_Count",
            "Average_Article_Score",
            "Max_Article_Score",
            "Years",
            "Example_Titles",
            "Journals",
        ]).to_csv(AUTHOR_AGG_FILE, index=False)
        print("No authors passed MIN_ARTICLE_SCORE.")
        return

    author_agg = (
        filtered_author_df
        .groupby("Author")
        .agg(
            Author_Expertise_Score=("Weighted Author Score", "sum"),
            Relevant_Paper_Count=("PMID", "count"),
            Average_Article_Score=("Article Relevance Score", "mean"),
            Max_Article_Score=("Article Relevance Score", "max"),
            Years=("Year", lambda x: "; ".join(sorted(set(str(y) for y in x)))),
            Example_Titles=("Title", lambda x: " || ".join(list(x)[:3])),
            Journals=("Journal", lambda x: "; ".join(sorted(set(str(j) for j in x))[:5])),
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
