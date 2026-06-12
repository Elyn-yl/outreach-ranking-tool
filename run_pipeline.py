import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RUN_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else BASE_DIR
RUN_DIR.mkdir(parents=True, exist_ok=True)

SOURCE_SCRIPT_NAMES = [
    "01_pubmed_search+aggregation.py",
    "02_outreach_enrichment.py",
    "03_1_pubmed_email_extractor.py",
    "04_email_enrichment_final_agg.py",
]

READ_EXCEL_CONFIG_SOURCE = BASE_DIR / "read_excel_config.py"
READ_EXCEL_CONFIG_RUN = RUN_DIR / "read_excel_config.py"

CONFIG_TEMPLATE = RUN_DIR / "config_template_user_input.xlsx"
CONFIG_FILE = RUN_DIR / "config.py"
FINAL_OUTPUT = RUN_DIR / "outreach_rankings.xlsx"
ARCHIVE_DIR = RUN_DIR / "intermediate_outputs"

INTERMEDIATE_FILES = [
    "papers_scored.csv",
    "ibd_papers_scored.csv",
    "author_aggregation.csv",
    "author_aggregation_v2.csv",
    "author_affiliation_summary.csv",
    "author_email_candidates.csv",
    "author_email_summary.csv",
    "faculty_email_candidates.csv",
    "publisher_corresponding_emails.csv",
    "author_aggregation_master.csv",
]


def prepare_run_folder() -> list[Path]:
    if not READ_EXCEL_CONFIG_SOURCE.exists():
        raise FileNotFoundError(f"Missing script: {READ_EXCEL_CONFIG_SOURCE}")

    shutil.copy2(READ_EXCEL_CONFIG_SOURCE, READ_EXCEL_CONFIG_RUN)
    run_scripts = [READ_EXCEL_CONFIG_RUN]

    for name in SOURCE_SCRIPT_NAMES:
        source = BASE_DIR / name
        destination = RUN_DIR / name

        if not source.exists():
            raise FileNotFoundError(f"Missing script: {source}")

        shutil.copy2(source, destination)
        run_scripts.append(destination)

    return run_scripts


def run_script(script_path: Path) -> None:
    print(f"\nRunning {script_path.name}...")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(RUN_DIR) + os.pathsep + env.get("PYTHONPATH", "")

    args = [sys.executable, str(script_path)]
    if script_path.name == "read_excel_config.py":
        args.append(str(RUN_DIR))

    result = subprocess.run(args, cwd=RUN_DIR, env=env)

    if result.returncode != 0:
        raise RuntimeError(f"{script_path.name} failed.")

    print(f"Finished {script_path.name}")


def archive_intermediates() -> None:
    ARCHIVE_DIR.mkdir(exist_ok=True)

    for file_name in INTERMEDIATE_FILES:
        source = RUN_DIR / file_name
        if not source.exists():
            continue

        destination = ARCHIVE_DIR / file_name
        if destination.exists():
            destination.unlink()

        shutil.move(str(source), str(destination))

    print(f"\nMoved intermediate files to: {ARCHIVE_DIR}")


def main() -> None:
    if not CONFIG_TEMPLATE.exists():
        raise FileNotFoundError(f"Missing {CONFIG_TEMPLATE.name}. Put it in the run folder.")

    scripts = prepare_run_folder()

    for script in scripts:
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
