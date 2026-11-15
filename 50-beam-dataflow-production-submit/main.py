# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Entry point: submit (or dry-run) a Beam pipeline to the Dataflow service.

DRY_RUN=True by default — prints the full job configuration and cost warnings
without submitting anything or incurring any charges.

To actually submit to Dataflow:
  1. Run deploy.sh to enable APIs, create GCS bucket, and upload data
  2. Set DRY_RUN=false:
       DRY_RUN=false python main.py

Usage:
  # Dry run (default — no cost, no GCP project required):
  python main.py

  # Real Dataflow submission (creates and bills worker VMs):
  GOOGLE_CLOUD_PROJECT=your-project-id \\
  GCS_BUCKET=your-gcs-bucket \\
  DRY_RUN=false python main.py
"""

import os

from dataflow_submit import run_dataflow_job, DRY_RUN

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")
REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")


def main():
    print("=" * 70)
    print("Apache Beam → Dataflow Production Submit")
    print(f"DRY_RUN     : {DRY_RUN}")
    print(f"Project     : {PROJECT_ID}")
    print(f"Region      : {REGION}")
    print(f"GCS Bucket  : {GCS_BUCKET}")
    print("=" * 70)

    if not DRY_RUN and PROJECT_ID == "your-project-id":
        print("\n[ERROR] Set GOOGLE_CLOUD_PROJECT before submitting a real job.")
        print("  export GOOGLE_CLOUD_PROJECT=your-project-id")
        print("  export GCS_BUCKET=your-gcs-bucket")
        return

    result = run_dataflow_job(
        project_id=PROJECT_ID,
        region=REGION,
        gcs_bucket=GCS_BUCKET,
        dry_run=DRY_RUN,
    )

    print("\n" + "=" * 70)
    if result["dry_run"]:
        print("Dry run complete. No job submitted, no cost incurred.")
        print("Set DRY_RUN=false to submit a real Dataflow job.")
    else:
        print(f"Job submitted: {result['job_name']}")
        print(f"Job ID       : {result['job_id']}")
        print(f"Output       : {result['gcs_paths']['output']}-*.txt")
    print("=" * 70)


if __name__ == "__main__":
    main()
