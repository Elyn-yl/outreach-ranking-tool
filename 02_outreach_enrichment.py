import pandas as pd
import re

# =========================
# 1. Read Author Aggregation
# =========================

input_file = "author_aggregation.csv"
output_file = "author_aggregation_v2.csv"

df = pd.read_csv(input_file)


# =========================
# 2. Helper Functions
# =========================

def clean_text(value):
    if pd.isna(value):
        return ""
    return str(value).lower()


def detect_interest_signals(row):
    text = (
        clean_text(row.get("Example_Titles", "")) + " " +
        clean_text(row.get("Journals", ""))
    )

    signals = []
    interest_score = 0

    if "therapeutic drug monitoring" in text or "tdm" in text:
        interest_score += 10
        signals.append("TDM Research")

    if "anti-drug antibody" in text or "anti drug antibody" in text:
        interest_score += 10
        signals.append("Anti-drug Antibodies")

    if "immunogenicity" in text:
        interest_score += 8
        signals.append("Immunogenicity")

    if "infliximab" in text:
        interest_score += 6
        signals.append("Infliximab")

    if "adalimumab" in text:
        interest_score += 6
        signals.append("Adalimumab")

    if "vedolizumab" in text:
        interest_score += 6
        signals.append("Vedolizumab")

    if "ustekinumab" in text:
        interest_score += 6
        signals.append("Ustekinumab")

    if "pediatric" in text or "paediatric" in text or "children" in text:
        interest_score += 8
        signals.append("Pediatric IBD")

    if "precision medicine" in text:
        interest_score += 8
        signals.append("Precision Medicine")

    if "clinical trial" in text or "trial" in text:
        interest_score += 5
        signals.append("Clinical Trial")

    if "biologic" in text or "biologics" in text:
        interest_score += 5
        signals.append("Biologics")

    return interest_score, "; ".join(signals)


def make_search_queries(author):
    return {
        "Faculty Search Query": f'"{author}" gastroenterology faculty',
        "Email Search Query": f'"{author}" email gastroenterology',
        "IBD Program Query": f'"{author}" IBD center inflammatory bowel disease',
        "Pediatric GI Query": f'"{author}" pediatric gastroenterology IBD',
        "Clinical Trial Query": f'"{author}" clinical trial inflammatory bowel disease',
        "Industry Collaboration Query": f'"{author}" advisory board consultant pharma IBD',
        "TDM Query": f'"{author}" therapeutic drug monitoring infliximab',
        "Google Scholar Query": f'"{author}" inflammatory bowel disease'
    }


def normalize_score(series):
    max_value = series.max()
    if max_value == 0:
        return series
    return series / max_value * 100


# =========================
# 3. Build Outreach Table
# =========================

interest_scores = []
interest_signals = []
query_rows = []

for _, row in df.iterrows():
    score, signals = detect_interest_signals(row)
    interest_scores.append(score)
    interest_signals.append(signals)

    author = row["Author"]
    query_rows.append(make_search_queries(author))

query_df = pd.DataFrame(query_rows)

df["ImmPro Interest Score"] = interest_scores
df["Interest Signals"] = interest_signals

# =========================
# 4. Manual Research Columns
# =========================

df["Institution"] = ""
df["Department"] = ""
df["Email"] = ""
df["Manual Notes"] = ""

# =========================
# 5. Scoring Columns
# =========================

df["Expertise Score Normalized"] = normalize_score(df["Author_Expertise_Score"])

df["Interest Score Normalized"] = normalize_score(df["ImmPro Interest Score"])

df["Strategic Fit Score"] = (
    0.7 * df["Expertise Score Normalized"] +
    0.3 * df["Interest Score Normalized"]
)

# This is intentionally separate.
# Do NOT use response likelihood to suppress high-value authors.
df["Accessibility Score"] = ""

# =========================
# 6. Combine Query Columns
# =========================

final_df = pd.concat([df, query_df], axis=1)

# =========================
# 7. Final Sort
# =========================

final_df = final_df.sort_values(
    by="Strategic Fit Score",
    ascending=False
)

# =========================
# 8. Keep Clean Columns Only
# =========================

keep_cols = [
    "Author",
    "Institution",
    "Department",
    "Email",
    "Author_Expertise_Score",
    "Relevant_Paper_Count",
    "Average_Article_Score",
    "Max_Article_Score",
    "Years",
    "ImmPro Interest Score",
    "Interest Signals",
    "Strategic Fit Score",
    "Example_Titles",
    "Journals",
    "Faculty Search Query",
    "Email Search Query",
    "IBD Program Query",
    "Manual Notes",
]

keep_cols = [col for col in keep_cols if col in final_df.columns]
final_df = final_df[keep_cols]

# =========================
# 8. Create Three Ranking Sheets
# =========================

clean_cols = [
    "Author",
    "Institution",
    "Department",
    "Email",
    "Author_Expertise_Score",
    "Relevant_Paper_Count",
    "Average_Article_Score",
    "Max_Article_Score",
    "Years",
    "ImmPro Interest Score",
    "Interest Signals",
    "Strategic Fit Score",
    "Example_Titles",
    "Journals",
    "Faculty Search Query",
    "Email Search Query",
    "IBD Program Query",
    "Manual Notes",
]

clean_cols = [col for col in clean_cols if col in final_df.columns]
clean_df = final_df[clean_cols].copy()

top_experts = clean_df.sort_values(
    by="Author_Expertise_Score",
    ascending=False
)

top_immpro_fits = clean_df.sort_values(
    by="ImmPro Interest Score",
    ascending=False
)

balanced_ranking = clean_df.sort_values(
    by="Strategic Fit Score",
    ascending=False
)

output_excel = "outreach_rankings.xlsx"

with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
    top_experts.to_excel(
        writer,
        sheet_name="Top Experts",
        index=False
    )

    top_immpro_fits.to_excel(
        writer,
        sheet_name="Top ImmPro Fits",
        index=False
    )

    balanced_ranking.to_excel(
        writer,
        sheet_name="Balanced Ranking",
        index=False
    )

print(f"Saved {output_excel}")