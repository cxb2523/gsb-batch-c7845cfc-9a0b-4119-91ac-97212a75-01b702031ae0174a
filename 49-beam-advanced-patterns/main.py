# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Entry point: demonstrates two advanced Beam patterns back-to-back.

  1. Branching with Tagged Side Outputs (dead-letter pattern)
     Splits sales rows into valid / invalid streams using TaggedOutput.
     Invalid rows are captured in a dead-letter branch instead of crashing
     the pipeline.

  2. Event-Time Windowing (FixedWindows)
     Assigns event-time timestamps from the event_timestamp field and sums
     revenue per 30-day window. Same windowing code works on Pub/Sub streaming.

Both pipelines run on DirectRunner — no GCP project or credentials required.
"""

import os
from pathlib import Path

from branching import run_branching_pipeline
from windowing import run_windowing_pipeline

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

BASE_DIR = Path(__file__).resolve().parent
SALES_CSV = BASE_DIR / "data" / "sales_data.csv"
EVENTS_JSON = BASE_DIR / "data" / "events.json"


def main():
    print("=" * 70)
    print("Apache Beam Advanced Patterns — DirectRunner Demos")
    print(f"Project (for DataflowRunner reference): {PROJECT_ID}")
    print("=" * 70)

    # --- Pattern 1: Branching + Dead-Letter ---
    print("\n[1/2] Branching with Tagged Side Outputs (dead-letter pattern)")
    counts = run_branching_pipeline(csv_path=str(SALES_CSV))

    # --- Pattern 2: Event-Time Windowing ---
    print("\n[2/2] Event-Time Windowing — 30-day FixedWindows, revenue per window")
    run_windowing_pipeline(events_file=str(EVENTS_JSON))

    print("\n" + "=" * 70)
    print("Summary")
    print(f"  Branching : {counts['valid']} valid rows, {counts['invalid']} dead-letter rows")
    print("  Windowing : see window results above")
    print()
    print("Production paths:")
    print("  Dead-letter  → WriteToBigQuery(dead_letter_table) + Cloud Monitoring alert")
    print("  Windowing    → ReadFromPubSub(timestamp_attribute='event_time') + Dataflow Streaming")
    print("=" * 70)


if __name__ == "__main__":
    main()
