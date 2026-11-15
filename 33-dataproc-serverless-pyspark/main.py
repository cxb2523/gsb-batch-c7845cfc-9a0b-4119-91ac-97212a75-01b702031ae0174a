# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Dataproc Serverless PySpark — demo entry point.

Stages a PySpark word-count script to GCS and submits it as a Dataproc
Serverless batch (no cluster management required).

DEFAULT: DRY_RUN=true — stages the script and prints batch parameters
without submitting. No Dataproc charges in dry-run mode; GCS write still
occurs (negligible cost for the tiny script file).

To submit a REAL paid Dataproc batch:
    export DRY_RUN=false
    python main.py

COST WARNING: Serverless bills per DCU-hour with a ~2 DCU / 1-minute
minimum per batch. Do not loop submissions.
"""

import os

from spark_job import stage_pyspark_script, submit_batch, DRY_RUN, PROJECT_ID, REGION, BUCKET


def main():
    print("=" * 65)
    print("  GCP DATA ENGINEERING HUB — Project 33")
    print("  Dataproc Serverless PySpark")
    print("=" * 65)
    print(f"\n  Project  : {PROJECT_ID}")
    print(f"  Region   : {REGION}")
    print(f"  Bucket   : gs://{BUCKET}")
    print(f"  DRY_RUN  : {DRY_RUN}")
    if DRY_RUN:
        print("\n  [INFO] Running in DRY RUN mode. No Dataproc batch will be submitted.")
        print("  [INFO] Set DRY_RUN=false to submit a real paid batch.\n")
    else:
        print("\n  [WARNING] DRY_RUN is FALSE — a real paid batch WILL be submitted.\n")

    print("-" * 65)

    # Step 1: Stage script to GCS (always runs — tiny file, negligible cost)
    print("[STEP 1] Staging PySpark script to GCS...")
    script_uri = stage_pyspark_script(
        bucket_name=BUCKET,
        project_id=PROJECT_ID,
    )

    # Step 2: Submit batch (skipped if DRY_RUN=true)
    print("\n[STEP 2] Submitting Dataproc Serverless batch...")
    result = submit_batch(
        project_id=PROJECT_ID,
        region=REGION,
        script_uri=script_uri,
        dry_run=DRY_RUN,
    )

    print("\n" + "=" * 65)
    if result.get("dry_run"):
        print("  Result: DRY RUN complete — no batch submitted.")
    else:
        print(f"  Result: Batch submitted successfully.")
        print(f"  Batch ID: {result.get('batch_id')}")
    print("=" * 65)


if __name__ == "__main__":
    main()
