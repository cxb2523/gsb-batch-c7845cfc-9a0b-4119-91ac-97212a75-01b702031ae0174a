"""
BigQuery CSV Ingestion Pipeline — entry point.

Demonstrates three ingestion modes:
  local   — streams a local CSV file into BigQuery (load_table_from_file)
  gcs     — loads CSV from Cloud Storage with an explicit schema (load_table_from_uri)
  formats — uploads JSONL + Parquet fixtures to GCS, then loads both into BigQuery

Usage:
    python main.py                  # runs all three modes
    python main.py --mode local
    python main.py --mode gcs
    python main.py --mode formats
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import argparse
import sys

from ingest_local import load_csv_local
from ingest_gcs import load_csv_from_gcs
from ingest_formats import load_json_and_parquet


MODES = {
    "local": load_csv_local,
    "gcs": load_csv_from_gcs,
    "formats": load_json_and_parquet,
}

SEPARATOR = "-" * 60


def run_mode(name: str, fn) -> None:
    print(f"\n{SEPARATOR}")
    print(f"MODE: {name.upper()}")
    print(SEPARATOR)
    try:
        fn()
    except Exception as exc:
        print(f"[ERROR] {name} mode failed: {exc}")


def main():
    parser = argparse.ArgumentParser(
        description="BigQuery CSV Ingestion Pipeline — three ingestion modes"
    )
    parser.add_argument(
        "--mode",
        choices=list(MODES.keys()),
        default=None,
        help="Ingestion mode to run. Omit to run all three.",
    )
    args = parser.parse_args()

    if args.mode:
        run_mode(args.mode, MODES[args.mode])
    else:
        print("Running all ingestion modes ...\n")
        for name, fn in MODES.items():
            run_mode(name, fn)

    print(f"\n{SEPARATOR}")
    print("All selected modes complete.")


if __name__ == "__main__":
    main()
