import sys
import pandas as pd
from pathlib import Path
from datetime import date, timedelta

BASE_DIR = Path(__file__).resolve().parent
RUN_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else BASE_DIR
EXCEL_FILE = RUN_DIR / "config_template_user_input.xlsx"
OUTPUT_CONFIG = RUN_DIR / "config.py"


def clean(x):
    if pd.isna(x):
        return ""
    text = str(x).strip()
    if text.lower() in ["nan", "none"]:
        return ""
    return text


def parse_int(value, default):
    try:
        if pd.isna(value) or str(value).strip() == "":
            return default
        return int(float(value))
    except Exception:
        return default


def read_project():
    df = pd.read_excel(EXCEL_FILE, sheet_name="Project")

    config = {}

    for _, row in df.iterrows():
        parameter = clean(row.get("Parameter", ""))
        value = clean(row.get("Value", ""))

        if parameter:
            config[parameter] = value

    today = date.today()
    default_end = today.strftime("%Y/%m/%d")
    default_start = (today - timedelta(days=5 * 365)).strftime("%Y/%m/%d")

    project_name = config.get("Project Name", "") or "Custom Project"
    ncbi_email = config.get("NCBI Email", "") or "your_email@example.com"

    start_date = config.get("Start Date", "") or default_start
    end_date = config.get("End Date", "") or default_end

    retmax = parse_int(config.get("Max Papers", ""), 300)
    min_article_score = parse_int(config.get("Min Article Score", ""), 10)

    return {
        "PROJECT_NAME": project_name,
        "NCBI_EMAIL": ncbi_email,
        "START_DATE": start_date,
        "END_DATE": end_date,
        "RETMAX": retmax,
        "MIN_ARTICLE_SCORE": min_article_score,
    }


def read_search_terms():
    df = pd.read_excel(EXCEL_FILE, sheet_name="Search Terms")

    terms = []

    for _, row in df.iterrows():
        term = clean(row.get("User Term", ""))
        include = clean(row.get("Include?", "")).lower()

        if not term:
            continue

        if include in ["yes", "y", "true", "1", ""]:
            if term not in terms:
                terms.append(term)

    return terms


def read_relevance_scores():
    df = pd.read_excel(EXCEL_FILE, sheet_name="Relevance Scores")

    relevance_terms = {}

    for _, row in df.iterrows():
        keyword = clean(row.get("User Keyword", "")).lower()
        score = row.get("User Score", "")

        if not keyword:
            continue

        relevance_terms[keyword] = parse_int(score, 5)

    return relevance_terms


def read_outreach_signals():
    df = pd.read_excel(EXCEL_FILE, sheet_name="Outreach Signals")

    signal_terms = {}
    signal_weights = {}

    for _, row in df.iterrows():
        group = clean(row.get("User Signal Group", ""))
        keyword = clean(row.get("User Keyword", "")).lower()
        score = parse_int(row.get("User Score", ""), 5)

        if not keyword:
            continue

        if not group:
            group = "Custom Signals"

        if group not in signal_terms:
            signal_terms[group] = []
            signal_weights[group] = score

        if keyword not in signal_terms[group]:
            signal_terms[group].append(keyword)

        signal_weights[group] = max(signal_weights.get(group, 5), score)

    return signal_terms, signal_weights


def write_config(project, search_terms, relevance_terms, outreach_signal_terms, outreach_signal_weights):
    content = f"""# config.py
# Auto-generated from config_template_user_input.xlsx

PROJECT_NAME = {project["PROJECT_NAME"]!r}

SEARCH_TERMS = {search_terms!r}

RELEVANCE_TERMS = {relevance_terms!r}

OUTREACH_SIGNAL_TERMS = {outreach_signal_terms!r}
OUTREACH_SIGNAL_WEIGHTS = {outreach_signal_weights!r}

START_DATE = {project["START_DATE"]!r}
END_DATE = {project["END_DATE"]!r}

RETMAX = {project["RETMAX"]}

MIN_ARTICLE_SCORE = {project["MIN_ARTICLE_SCORE"]}

AUTHOR_WEIGHTS = {{
    "single": 1.0,
    "first": 0.8,
    "last": 0.9,
    "middle": 0.2,
}}

NCBI_EMAIL = {project["NCBI_EMAIL"]!r}

PAPERS_SCORED_FILE = {str(RUN_DIR / "papers_scored.csv")!r}
AUTHOR_AGG_FILE = {str(RUN_DIR / "author_aggregation.csv")!r}
AUTHOR_AGG_V2_FILE = {str(RUN_DIR / "author_aggregation_v2.csv")!r}
AUTHOR_EMAIL_CANDIDATES_FILE = {str(RUN_DIR / "author_email_candidates.csv")!r}
AUTHOR_EMAIL_SUMMARY_FILE = {str(RUN_DIR / "author_email_summary.csv")!r}
AUTHOR_AFFILIATION_SUMMARY_FILE = {str(RUN_DIR / "author_affiliation_summary.csv")!r}
AUTHOR_MASTER_FILE = {str(RUN_DIR / "author_aggregation_master.csv")!r}
FINAL_XLSX = {str(RUN_DIR / "outreach_rankings.xlsx")!r}

AUTHOR_AGG_MASTER_FILE = AUTHOR_MASTER_FILE
FINAL_OUTPUT_FILE = FINAL_XLSX
"""

    with open(OUTPUT_CONFIG, "w", encoding="utf-8") as f:
        f.write(content)


def validate(search_terms, relevance_terms):
    errors = []

    if not search_terms:
        errors.append("No search terms found. Fill at least one User Term in Search Terms sheet.")

    if not relevance_terms:
        errors.append("No relevance keywords found. Fill at least one User Keyword in Relevance Scores sheet.")

    return errors


def main():
    if not EXCEL_FILE.exists():
        raise FileNotFoundError(
            f"Missing {EXCEL_FILE.name}. Put it in the run folder."
        )

    project = read_project()
    search_terms = read_search_terms()
    relevance_terms = read_relevance_scores()
    outreach_signal_terms, outreach_signal_weights = read_outreach_signals()

    errors = validate(search_terms, relevance_terms)

    if errors:
        print("Config error:")
        for e in errors:
            print(f"- {e}")
        raise SystemExit(1)

    write_config(
        project=project,
        search_terms=search_terms,
        relevance_terms=relevance_terms,
        outreach_signal_terms=outreach_signal_terms,
        outreach_signal_weights=outreach_signal_weights,
    )

    print("Config generated successfully.")
    print(f"Project: {project['PROJECT_NAME']}")
    print(f"Search terms: {len(search_terms)}")
    print(f"Relevance keywords: {len(relevance_terms)}")
    print(f"Outreach signal groups: {len(outreach_signal_terms)}")
    print(f"Saved: {OUTPUT_CONFIG}")


if __name__ == "__main__":
    main()
