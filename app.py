import os
import sys
import subprocess
import tempfile
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import streamlit as st


BASE_DIR = Path(__file__).resolve().parent

DEFAULT_START = date.today() - timedelta(days=5 * 365)
DEFAULT_END = date.today()


DEFAULT_SEARCH_USER = pd.DataFrame({
    "User Term": [""] * 8,
    "Include?": ["yes"] * 8,
})

IBD_SEARCH_REF = pd.DataFrame({
    "IBD Reference Term": [
        "Inflammatory Bowel Disease",
        "IBD",
        "Crohn Disease",
        "Ulcerative Colitis",
    ],
    "Example Meaning": [
        "Disease umbrella term",
        "Common abbreviation",
        "Subtype",
        "Subtype",
    ],
    "Use?": ["yes", "yes", "yes", "yes"],
})


DEFAULT_RELEVANCE_USER = pd.DataFrame({
    "User Keyword": [""] * 12,
    "User Score": [""] * 12,
})

IBD_RELEVANCE_REF = pd.DataFrame({
    "IBD Reference Keyword": [
        "therapeutic drug monitoring",
        "tdm",
        "anti-drug antibody",
        "immunogenicity",
        "biologics",
        "infliximab",
        "adalimumab",
        "vedolizumab",
        "ustekinumab",
        "precision medicine",
        "pediatric",
    ],
    "Reference Score": [10, 10, 10, 8, 8, 8, 8, 8, 8, 5, 5],
    "Reference Meaning": [
        "Highest priority",
        "Abbreviation",
        "Highest priority",
        "Important mechanism",
        "Relevant drug class",
        "Biologic drug",
        "Biologic drug",
        "Biologic drug",
        "Biologic drug",
        "Secondary signal",
        "Secondary signal",
    ],
})


DEFAULT_OUTREACH_USER = pd.DataFrame({
    "User Signal Group": [""] * 12,
    "User Keyword": [""] * 12,
    "User Score": [""] * 12,
})

IBD_OUTREACH_REF = pd.DataFrame({
    "IBD Reference Signal Group": [
        "TDM Research",
        "TDM Research",
        "Anti-drug Antibodies",
        "Immunogenicity",
        "Biologics",
        "Pediatric",
        "Clinical Trial",
        "Industry",
        "Physician Type",
    ],
    "IBD Reference Keyword": [
        "therapeutic drug monitoring",
        "tdm",
        "anti-drug antibody",
        "immunogenicity",
        "biologics",
        "pediatric",
        "clinical trial",
        "industry sponsored",
        "md phd",
    ],
    "Reference Score": [10, 10, 10, 8, 8, 8, 5, 5, 5],
    "Reference Meaning": [
        "Strong fit",
        "Strong fit",
        "Strong fit",
        "Strong fit",
        "Relevant drug class",
        "Outreach preference",
        "Useful signal",
        "Useful signal",
        "Useful signal",
    ],
})


def safe_text(value, default=""):
    if pd.isna(value):
        return default
    text = str(value).strip()
    if text.lower() in ["nan", "none"]:
        return default
    return text


def clean_user_columns(df: pd.DataFrame, wanted_cols: list[str], fallback: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        df = fallback.copy()

    for col in wanted_cols:
        if col not in df.columns:
            df[col] = ""

    df = df[wanted_cols].copy()
    df = df.fillna("")

    for col in wanted_cols:
        df[col] = (
            df[col]
            .astype(str)
            .replace(["nan", "None"], "")
        )

    return df


def get_blank_project():
    return {
        "project_name": "",
        "ncbi_email": "",
        "start_date": DEFAULT_START,
        "end_date": DEFAULT_END,
        "max_papers": 300,
        "min_article_score": 10,
    }


def init_session_state():
    # Each browser session gets its own blank state.
    # This prevents different users from seeing or overwriting one another's inputs.
    if "project" not in st.session_state:
        st.session_state.project = get_blank_project()
    if "search" not in st.session_state:
        st.session_state.search = DEFAULT_SEARCH_USER.copy()
    if "relevance" not in st.session_state:
        st.session_state.relevance = DEFAULT_RELEVANCE_USER.copy()
    if "outreach" not in st.session_state:
        st.session_state.outreach = DEFAULT_OUTREACH_USER.copy()


def build_project_sheet(project_name, ncbi_email, start_date, end_date, max_papers, min_article_score):
    return pd.DataFrame({
        "Parameter": [
            "Project Name",
            "NCBI Email",
            "Start Date",
            "End Date",
            "Max Papers",
            "Min Article Score",
        ],
        "Value": [
            project_name,
            ncbi_email,
            start_date.strftime("%Y/%m/%d"),
            end_date.strftime("%Y/%m/%d"),
            int(max_papers),
            int(min_article_score),
        ],
        "Default / Notes": [
            "Required: enter disease/project name",
            "Required: email used for PubMed requests",
            "Default: 5 years ago; editable",
            "Default: today; editable",
            "Default: 300; increase for broad fields",
            "Default: 10; minimum paper relevance score to include authors",
        ],
    })


def save_config_excel(config_xlsx: Path, project_df, search_df, relevance_df, outreach_df):
    search_df = clean_user_columns(search_df, ["User Term", "Include?"], DEFAULT_SEARCH_USER)
    relevance_df = clean_user_columns(relevance_df, ["User Keyword", "User Score"], DEFAULT_RELEVANCE_USER)
    outreach_df = clean_user_columns(outreach_df, ["User Signal Group", "User Keyword", "User Score"], DEFAULT_OUTREACH_USER)

    with pd.ExcelWriter(config_xlsx, engine="openpyxl") as writer:
        project_df.to_excel(writer, sheet_name="Project", index=False)

        search_out = pd.concat(
            [search_df.reset_index(drop=True), IBD_SEARCH_REF.reset_index(drop=True)],
            axis=1,
        )
        search_out.to_excel(writer, sheet_name="Search Terms", index=False)

        relevance_out = pd.concat(
            [relevance_df.reset_index(drop=True), IBD_RELEVANCE_REF.reset_index(drop=True)],
            axis=1,
        )
        relevance_out.to_excel(writer, sheet_name="Relevance Scores", index=False)

        outreach_out = pd.concat(
            [outreach_df.reset_index(drop=True), IBD_OUTREACH_REF.reset_index(drop=True)],
            axis=1,
        )
        outreach_out.to_excel(writer, sheet_name="Outreach Signals", index=False)


def run_pipeline(run_dir: Path):
    run_pipeline_file = BASE_DIR / "run_pipeline.py"
    return subprocess.run(
        [sys.executable, str(run_pipeline_file), str(run_dir)],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
    )


def validate_inputs(project_name, ncbi_email, search_df, relevance_df):
    errors = []

    if not safe_text(project_name):
        errors.append("Project Name is required.")

    if "@" not in safe_text(ncbi_email):
        errors.append("A valid NCBI Email is required.")

    search_terms = search_df["User Term"].fillna("").astype(str).str.strip()
    if search_terms[search_terms != ""].empty:
        errors.append("Add at least one Search Term.")

    relevance_terms = relevance_df["User Keyword"].fillna("").astype(str).str.strip()
    if relevance_terms[relevance_terms != ""].empty:
        errors.append("Add at least one Relevance Keyword.")

    return errors


st.set_page_config(page_title="Outreach Ranking Tool", page_icon="📊", layout="wide")

init_session_state()

st.title("Outreach Ranking Tool")
st.caption("Enter project settings and scoring keywords, then generate an outreach ranking Excel file.")

project = st.session_state.project

st.subheader("1. Project Settings")

col1, col2 = st.columns(2)

with col1:
    project_name = st.text_input(
        "Project Name",
        value=project["project_name"],
        help="Disease area or outreach project name, e.g. Multiple Myeloma.",
    )
    ncbi_email = st.text_input(
        "NCBI Email",
        value=project["ncbi_email"],
        help="Required by NCBI/PubMed requests. Use a real contact email.",
    )
    start_date = st.date_input(
        "Start Date",
        value=project["start_date"],
        help="Publication date lower bound.",
    )

with col2:
    end_date = st.date_input(
        "End Date",
        value=project["end_date"],
        help="Publication date upper bound.",
    )
    max_papers = st.number_input(
        "Max Papers",
        min_value=1,
        max_value=5000,
        value=int(project["max_papers"]),
        step=50,
        help="Number of PubMed papers to retrieve. Use more for broad fields.",
    )
    min_article_score = st.number_input(
        "Min Article Score",
        min_value=0,
        max_value=1000,
        value=int(project["min_article_score"]),
        step=1,
        help="Minimum article relevance score required for author aggregation.",
    )

project_df = build_project_sheet(
    project_name,
    ncbi_email,
    start_date,
    end_date,
    max_papers,
    min_article_score,
)

st.subheader("2. Search Terms")
st.caption("Only User Term and Include? are used. IBD examples are shown below for guidance.")

search_input = st.session_state.search.copy()
search_input["User Term"] = (
    search_input["User Term"]
    .fillna("")
    .astype(str)
    .replace(["nan", "None"], "")
)
search_input["Include?"] = (
    search_input["Include?"]
    .fillna("yes")
    .astype(str)
    .replace(["nan", "None", ""], "yes")
)

search_df = st.data_editor(
    search_input,
    num_rows="dynamic",
    width="stretch",
    key="search_editor",
    column_config={
        "User Term": st.column_config.TextColumn("User Term", help="Disease/search term to include in PubMed query."),
        "Include?": st.column_config.SelectboxColumn("Include?", options=["yes", "no"], help="Use this term?"),
    },
)

with st.expander("View IBD reference search terms"):
    st.dataframe(IBD_SEARCH_REF, width="stretch", hide_index=True)

st.subheader("3. Relevance Scores")
st.caption("These keywords score paper relevance. Empty rows are skipped. Blank/non-numeric scores default to 5.")

relevance_input = (
    st.session_state.relevance
    .copy()
    .fillna("")
    .replace(["nan", "None"], "")
)

relevance_input["User Keyword"] = (
    relevance_input["User Keyword"]
    .astype(str)
    .replace(["nan", "None"], "")
)

relevance_input["User Score"] = (
    relevance_input["User Score"]
    .astype(str)
    .replace(["nan", "None"], "")
)

relevance_df = st.data_editor(
    relevance_input,
    num_rows="dynamic",
    width="stretch",
    key="relevance_editor",
    column_config={
        "User Keyword": st.column_config.TextColumn("User Keyword", help="Keyword used to score article relevance."),
        "User Score": st.column_config.SelectboxColumn("User Score", options=[""] + [str(i) for i in range(1, 11)], help="Priority score from 1 to 10. Blank defaults to 5."),
    },
)

with st.expander("View IBD reference relevance scores"):
    st.dataframe(IBD_RELEVANCE_REF, width="stretch", hide_index=True)

st.subheader("4. Outreach Signals")
st.caption("These keywords score author outreach fit. Empty rows are skipped. Blank/non-numeric scores default to 5.")

outreach_input = (
    st.session_state.outreach
    .copy()
    .fillna("")
    .replace(["nan", "None"], "")
)

outreach_input["User Signal Group"] = (
    outreach_input["User Signal Group"]
    .astype(str)
    .replace(["nan", "None"], "")
)

outreach_input["User Keyword"] = (
    outreach_input["User Keyword"]
    .astype(str)
    .replace(["nan", "None"], "")
)

outreach_input["User Score"] = (
    outreach_input["User Score"]
    .astype(str)
    .replace(["nan", "None"], "")
)

outreach_df = st.data_editor(
    outreach_input,
    num_rows="dynamic",
    width="stretch",
    key="outreach_editor",
    column_config={
        "User Signal Group": st.column_config.TextColumn("User Signal Group", help="Signal category, e.g. Clinical Trial."),
        "User Keyword": st.column_config.TextColumn("User Keyword", help="Keyword used to detect this signal."),
        "User Score": st.column_config.SelectboxColumn("User Score", options=[""] + [str(i) for i in range(1, 11)], help="Priority score from 1 to 10. Blank defaults to 5."),
    },
)

with st.expander("View IBD reference outreach signals"):
    st.dataframe(IBD_OUTREACH_REF, width="stretch", hide_index=True)

st.divider()

if st.button("Generate Outreach Ranking", type="primary"):
    errors = validate_inputs(project_name, ncbi_email, search_df, relevance_df)

    if errors:
        for error in errors:
            st.error(error)
        st.stop()

    # One isolated folder per run. This prevents users from overwriting each other.
    run_dir = Path(tempfile.mkdtemp(prefix="outreach_run_"))
    config_xlsx = run_dir / "config_template_user_input.xlsx"
    final_output = run_dir / "outreach_rankings.xlsx"

    save_config_excel(config_xlsx, project_df, search_df, relevance_df, outreach_df)

    with st.spinner("Running pipeline. This may take several minutes..."):
        result = run_pipeline(run_dir)

    if result.returncode == 0 and final_output.exists():
        st.success("Outreach ranking generated successfully.")

        with open(final_output, "rb") as f:
            st.download_button(
                label="Download outreach_rankings.xlsx",
                data=f,
                file_name="outreach_rankings.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        with st.expander("Pipeline log"):
            st.code(result.stdout)

    else:
        st.error("Pipeline failed.")
        with st.expander("Error log"):
            st.code(result.stderr or result.stdout)
