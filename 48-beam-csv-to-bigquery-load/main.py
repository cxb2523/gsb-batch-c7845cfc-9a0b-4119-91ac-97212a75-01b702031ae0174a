# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Entry point: runs the Beam CSV → BigQuery pipeline using DirectRunner.

Even on DirectRunner, WriteToBigQuery performs a REAL BigQuery load job.
Requires:
  - A GCP project with BigQuery and GCS enabled
  - A GCS bucket for staging temp files
  - The target BigQuery dataset to exist (deploy.sh creates it)

Usage:
  GOOGLE_CLOUD_PROJECT=your-project-id \\
  GCS_BUCKET=your-gcs-bucket \\
  python main.py
"""

import os
from pathlib import Path

from schema import print_schema
from pipeline import run_pipeline

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")
DATASET = "your_dataset"

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "data" / "sales_data.csv"


def main():
    print("=" * 70)
    print("Apache Beam CSV → BigQuery Load")
    print(f"Project : {PROJECT_ID}")
    print(f"Dataset : {DATASET}")
    print(f"GCS Tmp : gs://{GCS_BUCKET}/beam-tmp/")
    print("=" * 70)

    if PROJECT_ID == "your-project-id":
        print("\n[WARNING] GOOGLE_CLOUD_PROJECT is not set.")
        print("Set it before running:")
        print("  export GOOGLE_CLOUD_PROJECT=your-project-id")
        print("  export GCS_BUCKET=your-gcs-bucket")
        return

    print("\nTarget table schema:")
    print_schema()

    print("\nStarting pipeline...")
    run_pipeline(
        csv_path=str(CSV_PATH),
        project_id=PROJECT_ID,
        gcs_bucket=GCS_BUCKET,
        dataset=DATASET,
    )

    print("\nNext steps:")
    print(f"  bq query --use_legacy_sql=false 'SELECT region, SUM(total_amount) FROM {DATASET}.beam_sales GROUP BY 1'")


if __name__ == "__main__":
    main()
