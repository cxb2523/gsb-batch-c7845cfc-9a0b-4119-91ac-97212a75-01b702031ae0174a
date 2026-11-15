# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Entry point: runs two Apache Beam pipelines back-to-back on DirectRunner.

  1. WordCount     — counts words from data/sample_text.txt
  2. CSV Transform — filters sales_data.csv to 'AI' category, sums revenue per region

Both pipelines run locally — no GCP project or credentials required.
The same pipeline code runs on Dataflow by changing the PipelineOptions runner.
"""

import os
from pathlib import Path

from wordcount import run_wordcount_demo
from csv_transform import run_csv_transform

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

BASE_DIR = Path(__file__).resolve().parent
TEXT_FILE = BASE_DIR / "data" / "sample_text.txt"
CSV_FILE = BASE_DIR / "data" / "sales_data.csv"


def main():
    print("=" * 70)
    print("Apache Beam Data Transformation — DirectRunner Demos")
    print(f"Project (for DataflowRunner reference): {PROJECT_ID}")
    print("=" * 70)

    # --- Pipeline 1: WordCount ---
    print("\n[1/2] WordCount pipeline")
    run_wordcount_demo(text_file=str(TEXT_FILE), top_n=20)

    # --- Pipeline 2: CSV Transform ---
    print("\n[2/2] CSV Transform pipeline — AI category revenue per region")
    run_csv_transform(
        csv_path=str(CSV_FILE),
        category_filter="AI",
    )

    print("\n" + "=" * 70)
    print("Both pipelines complete.")
    print("Output files written to: ./out/")
    print()
    print("To run on Dataflow instead of DirectRunner:")
    print("  1. Upload input file:  gsutil cp data/sales_data.csv gs://your-gcs-bucket/data/")
    print("  2. Change PipelineOptions in csv_transform.py:")
    print("       runner='DataflowRunner'")
    print("       project='your-project-id'")
    print("       region='us-central1'")
    print("       temp_location='gs://your-gcs-bucket/tmp/'")
    print("  3. Point csv_path at the gs:// URI")
    print("=" * 70)


if __name__ == "__main__":
    main()
