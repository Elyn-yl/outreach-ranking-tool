import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)

READ_EXCEL_CONFIG = BASE_DIR / "read_excel_config.py"
CONFIG_TEMPLATE = BASE_DIR / "config_template_user_input.xlsx"
CONFIG_FILE = BASE_DIR / "config.py"

SCRIPTS = [
    READ_EXCEL_CONFIG,
    BASE_DIR / "01_pubmed_search+aggregation.py",
    BASE_DIR / "02_outreach_enrichment.py",
    BASE_DIR / "03_1_pubmed_email_extractor.py",
    BASE_DIR / "04_email_enrichment_final_agg.py",
]

FINAL_OUTPUT = BASE_DIR / "outreach_rankings.xlsx"
ARCHIVE_DIR = BASE_DIR / "intermediate_outputs"

INTERMEDIATE_FILES = [
    "papers_scored.csv",
    "ibd_papers_scored.csv",
    "author_aggregation.csv",
    "author_aggregation_v2.csv",
    "author_affiliation_summary.csv",
    "author_email_candidates.csv",
    "author_email_summary.csv",
    "author_aggregation_master.csv",
]


def run_script(script_path: Path) -> None:
    print(f"\nRunning {script_path.name}...")

    if not script_path.exists():
        raise FileNotFoundError(f"Missing script: {script_path}")

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=BASE_DIR,
    )

    if result.returncode != 0:
        raise RuntimeError(f"{script_path.name} failed.")

    print(f"Finished {script_path.name}")


def archive_intermediates() -> None:
    ARCHIVE_DIR.mkdir(exist_ok=True)

    for file_name in INTERMEDIATE_FILES:
        source = BASE_DIR / file_name
        if not source.exists():
            continue

        destination = ARCHIVE_DIR / file_name

        if destination.exists():
            destination.unlink()

        shutil.move(str(source), str(destination))

    print(f"\nMoved intermediate files to: {ARCHIVE_DIR}")


def main() -> None:
    if not CONFIG_TEMPLATE.exists():
        raise FileNotFoundError(
            f"Missing {CONFIG_TEMPLATE.name}. Put it in the same folder as run_pipeline.py."
        )

    for script in SCRIPTS:
        run_script(script)

    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Config was not generated: {CONFIG_FILE}")

    if not FINAL_OUTPUT.exists():
        raise FileNotFoundError(f"Final output not found: {FINAL_OUTPUT}")

    archive_intermediates()

    print("\nDONE")
    print(f"Final deliverable: {FINAL_OUTPUT}")


if __name__ == "__main__":
    main()