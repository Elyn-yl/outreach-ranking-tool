from Bio import Entrez
import pandas as pd
import re

Entrez.email = "elynyu21@gmail.com"

# =========================
# 1. Search Settings
# =========================

search_term = """
(
    "Inflammatory Bowel Disease"[Title/Abstract]
    OR "Crohn Disease"[Title/Abstract]
    OR "Ulcerative Colitis"[Title/Abstract]
)
AND
(
    infliximab[Title/Abstract]
    OR adalimumab[Title/Abstract]
    OR vedolizumab[Title/Abstract]
    OR ustekinumab[Title/Abstract]
    OR biologic*[Title/Abstract]
    OR immunogenicity[Title/Abstract]
    OR "anti-drug antibody"[Title/Abstract]
    OR "therapeutic drug monitoring"[Title/Abstract]
    OR TDM[Title/Abstract]
)
AND
(
    "2021/06/09"[Date - Publication] : "2026/06/09"[Date - Publication]
)
"""

retmax = 100

# =========================
# 2. Keyword Score Settings
# =========================

keyword_scores = {
    "therapeutic drug monitoring": 10,
    "tdm": 10,
    "anti-drug antibody": 10,
    "anti drug antibody": 10,
    "immunogenicity": 8,
    "biologic": 8,
    "biologics": 8,
    "infliximab": 8,
    "adalimumab": 8,
    "ustekinumab": 8,
    "vedolizumab": 8,
    "precision medicine": 5,
    "pediatric": 5,
    "paediatric": 5,
    "child": 5,
    "children": 5,
}

def score_article(title, abstract):
    text_title = title.lower()
    text_abstract = abstract.lower()

    score = 0
    matched_keywords = []

    for keyword, points in keyword_scores.items():
        if keyword in text_title:
            score += points * 3
            matched_keywords.append(f"{keyword} (title)")

        if keyword in text_abstract:
            score += points
            matched_keywords.append(f"{keyword} (abstract)")

    return score, "; ".join(matched_keywords)

def get_author_weight(index, total_authors):
    if total_authors == 1:
        return 1.0, "single"

    if index == 0:
        return 0.8, "first"

    if index == total_authors - 1:
        return 0.9, "last"

    return 0.2, "middle"

# =========================
# 3. Search PubMed
# =========================

search = Entrez.esearch(
    db="pubmed",
    term=search_term,
    retmax=retmax,
    sort="relevance"
)

search_results = Entrez.read(search)
id_list = search_results["IdList"]

print(f"Found {len(id_list)} papers")

# =========================
# 4. Fetch Paper Details
# =========================

fetch = Entrez.efetch(
    db="pubmed",
    id=",".join(id_list),
    rettype="abstract",
    retmode="xml"
)

papers = Entrez.read(fetch)

paper_results = []
author_rows = []

# =========================
# 5. Extract Paper + Author Info
# =========================

for article in papers["PubmedArticle"]:
    citation = article["MedlineCitation"]
    article_info = citation["Article"]

    pmid = str(citation["PMID"])
    title = str(article_info.get("ArticleTitle", ""))

    try:
        abstract_list = article_info["Abstract"]["AbstractText"]
        abstract = " ".join(str(x) for x in abstract_list)
    except:
        abstract = "No abstract available"

    try:
        journal = str(article_info["Journal"]["Title"])
    except:
        journal = "Unknown"

    try:
        year = article_info["Journal"]["JournalIssue"]["PubDate"]["Year"]
    except:
        year = "Unknown"

    relevance_score, matched_keywords = score_article(title, abstract)

    authors_clean = []

    try:
        authors_list = article_info["AuthorList"]

        for i, author in enumerate(authors_list):
            last_name = author.get("LastName", "")
            fore_name = author.get("ForeName", "")

            full_name = f"{fore_name} {last_name}".strip()

            if full_name == "":
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
                "Matched Keywords": matched_keywords
            })

    except:
        authors_clean = []

    paper_results.append({
        "PMID": pmid,
        "Title": title,
        "Abstract": abstract,
        "Authors": "; ".join(authors_clean),
        "Journal": journal,
        "Year": year,
        "Relevance Score": relevance_score,
        "Matched Keywords": matched_keywords
    })

# =========================
# 6. Export Paper CSVs
# =========================

paper_df = pd.DataFrame(paper_results)

paper_df = paper_df.sort_values(
    by="Relevance Score",
    ascending=False
)

paper_df.to_csv("ibd_papers_scored.csv", index=False)

filtered_paper_df = paper_df[paper_df["Relevance Score"] >= 10]
filtered_paper_df.to_csv("ibd_papers_filtered.csv", index=False)

# =========================
# 7. Author Aggregation
# =========================

author_df = pd.DataFrame(author_rows)

filtered_author_df = author_df[author_df["Article Relevance Score"] >= 10]

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
        Journals=("Journal", lambda x: "; ".join(sorted(set(str(j) for j in x))[:5]))
    )
    .reset_index()
)

author_agg = author_agg.sort_values(
    by="Author_Expertise_Score",
    ascending=False
)

author_agg.to_csv("author_aggregation.csv", index=False)

# =========================
# 8. Print Preview
# =========================

print("Saved:")
print("1. ibd_papers_scored.csv")
print("2. ibd_papers_filtered.csv")
print("3. author_aggregation.csv")
print()
print(author_agg.head(20))