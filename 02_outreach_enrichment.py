import pandas as pd

from config import (
    PROJECT_NAME,
    SEARCH_TERMS,
    OUTREACH_SIGNAL_TERMS,
    OUTREACH_SIGNAL_WEIGHTS,
    AUTHOR_AGG_FILE,
    AUTHOR_AGG_V2_FILE,
)


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

    for signal_name, keywords in OUTREACH_SIGNAL_TERMS.items():
        matched = False
        for keyword in keywords:
            if str(keyword).lower() in text:
                matched = True
                break

        if matched:
            signals.append(signal_name)
            interest_score += OUTREACH_SIGNAL_WEIGHTS.get(signal_name, 6)

    return interest_score, "; ".join(signals)


def make_search_queries(author):
    field_phrase = " ".join(SEARCH_TERMS[:3]) if SEARCH_TERMS else PROJECT_NAME

    return {
        "Faculty Search Query": f'"{author}" faculty',
        "Email Search Query": f'"{author}" email',
        "Program Query": f'"{author}" "{field_phrase}"',
        "Clinical Trial Query": f'"{author}" clinical trial "{field_phrase}"',
        "Industry Collaboration Query": f'"{author}" advisory board consultant pharma "{field_phrase}"',
        "Google Scholar Query": f'"{author}" "{field_phrase}"',
    }


def normalize_score(series):
    max_value = series.max()
    if max_value == 0:
        return series
    return series / max_value * 100


def main():
    df = pd.read_csv(AUTHOR_AGG_FILE)

    if df.empty:
        df.to_csv(AUTHOR_AGG_V2_FILE, index=False, encoding="utf-8-sig")
        print(f"No rows found. Saved empty {AUTHOR_AGG_V2_FILE}")
        return

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
        "Program Query",
        "Clinical Trial Query",
        "Industry Collaboration Query",
        "Google Scholar Query",
        "Manual Notes",
    ]

    keep_cols = [col for col in keep_cols if col in final_df.columns]
    clean_df = final_df[keep_cols].copy()

    clean_df = clean_df.sort_values(
        by="Strategic Fit Score",
        ascending=False,
    )

    clean_df.to_csv(
        AUTHOR_AGG_V2_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Saved {AUTHOR_AGG_V2_FILE}")


if __name__ == "__main__":
    main()
