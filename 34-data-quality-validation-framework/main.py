# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Data Quality Validation Framework — entry point.

Loads the sales CSV dataset, runs all validation checks (schema, completeness,
ranges, uniqueness, consistency), prints a structured quality report, and
optionally saves a JSON report.

Exit code: 0 = PASS, 1 = FAIL — safe to wire into Cloud Run jobs, Composer
DAGs, or CI pipelines so the BQ load step is gated on data quality.
"""

import os
import sys
from pathlib import Path

import pandas as pd

from validators import run_all_checks
from reporter import print_report, build_report_dict, save_report_json

# Path to the validation dataset
CSV_PATH = Path(__file__).resolve().parent / "data" / "sales_data.csv"

# Optional: set SAVE_REPORT=true to persist a JSON report to disk
SAVE_REPORT = os.environ.get("SAVE_REPORT", "false").lower() == "true"
REPORT_PATH = Path(__file__).resolve().parent / "data" / "quality_report.json"


def load_dataset(csv_path=CSV_PATH):
    """
    Loads the CSV into a DataFrame, parsing the order_date column as datetime.

    Args:
        csv_path: Path to the CSV file.

    Returns:
        pd.DataFrame
    """
    df = pd.read_csv(csv_path, parse_dates=["order_date"])
    return df


def main():
    print("=" * 65)
    print("  GCP DATA ENGINEERING HUB — Project 34")
    print("  Data Quality Validation Framework")
    print("=" * 65)

    # Load
    df = load_dataset(CSV_PATH)

    # Run all checks
    results, failures = run_all_checks(df)

    # Print structured report
    print_report(CSV_PATH.name, len(df), results, failures)

    # Optional JSON export (SAVE_REPORT=true)
    if SAVE_REPORT:
        report = build_report_dict(CSV_PATH.name, len(df), results, failures)
        save_report_json(report, REPORT_PATH)

    # Non-zero exit on failure so orchestrators (Composer / Cloud Run jobs)
    # stop the pipeline before the BigQuery load step runs.
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
