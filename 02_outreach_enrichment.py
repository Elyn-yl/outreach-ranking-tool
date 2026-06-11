import pandas as pd

from config import (
    AUTHOR_AGG_FILE,
    AUTHOR_AGG_V2_FILE,
    OUTREACH_SIGNAL_TERMS,
    OUTREACH_SIGNAL_WEIGHTS,
)


def clean_text(value):
    if pd.isna(value):
        return ""
    return str(value).lower()


def normalize_score(series):
    max_value = series.max()
    if pd.isna(max_value) or max_value == 0:
        return series * 0
    return series / max_value * 100


def detect_interest_signals(row):
    text = (
        clean_text(row.get("Example_Titles", "")) + " " +
        clean_text(row.get("Journals", "")) + " " +
        clean_text(row.get("Matched_Keywords", "")) + " " +
        clean_text(row.get("Representative_Article_Titles", ""))
    )

    signals = []
    interest_score = 0

    for group, keywords in OUTREACH_SIGNAL_TERMS.items():
        group_score = OUTREACH_SIGNAL_WEIGHTS.get(group, 5)
        for keyword in keywords:
            keyword = str(keyword).lower().strip()
            if keyword and keyword in text:
                interest_score += group_score
                signals.append(group)
                break

    return interest_score, "; ".join(dict.fromkeys(signals))


def make_search_queries(author, institution=""):
    context = f" {institution}" if institution else ""
    return {
        "Faculty Search Query": f'"{author}"{context} faculty',
        "Email Search Query": f'"{author}"{context} email',
        "Program Query": f'"{author}" IBD inflammatory bowel disease',
        "Clinical Trial Query": f'"{author}" clinical trial',
        "Industry Collaboration Query": f'"{author}" advisory board consultant pharma',
        "Google Scholar Query": f'"{author}"',
    }


def main():
    df = pd.read_csv(AUTHOR_AGG_FILE)

    if df.empty:
        pd.DataFrame().to_csv(AUTHOR_AGG_V2_FILE, index=False)
        print(f"Saved empty {AUTHOR_AGG_V2_FILE}")
        return

    interest_scores = []
    interest_signals = []
    query_rows = []

    for _, row in df.iterrows():
        score, signals = detect_interest_signals(row)
        interest_scores.append(score)
        interest_signals.append(signals)

        author = row.get("Author", "")
        institution = row.get("Institution", "")
        query_rows.append(make_search_queries(author, institution))

    query_df = pd.DataFrame(query_rows)

    df["ImmPro Interest Score"] = interest_scores
    df["Interest Signals"] = interest_signals

    if "Institution" not in df.columns:
        df["Institution"] = ""
    if "Department" not in df.columns:
        df["Department"] = ""
    if "Email" not in df.columns:
        df["Email"] = ""
    if "Manual Notes" not in df.columns:
        df["Manual Notes"] = ""

    df["Expertise Score Normalized"] = normalize_score(df["Author_Expertise_Score"])
    df["Interest Score Normalized"] = normalize_score(df["ImmPro Interest Score"])

    df["Strategic Fit Score"] = (
        0.7 * df["Expertise Score Normalized"] +
        0.3 * df["Interest Score Normalized"]
    )

    df["Accessibility Score"] = ""

    final_df = pd.concat([df, query_df], axis=1)

    final_df = final_df.sort_values(
        by="Strategic Fit Score",
        ascending=False,
    )

    final_df.to_csv(AUTHOR_AGG_V2_FILE, index=False, encoding="utf-8-sig")
    print(f"Saved {AUTHOR_AGG_V2_FILE}")


if __name__ == "__main__":
    main()
