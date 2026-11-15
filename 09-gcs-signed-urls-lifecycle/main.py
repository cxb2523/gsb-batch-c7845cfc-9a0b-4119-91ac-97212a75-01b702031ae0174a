# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Orchestrator: GCS Signed URLs + Lifecycle Rules

Flow:
  1. Generate a V4 signed GET URL (15-minute expiry) for a GCS object.
  2. Generate a V4 signed PUT URL (pre-signed upload, 60-minute expiry).
  3. Print current lifecycle rules on the bucket.
  4. Apply standard lifecycle rules (auto-delete tmp/ after 7d, NEARLINE after 30d).
  5. Optionally clear the rules to leave the bucket as found.

Set env vars before running:
  export GOOGLE_CLOUD_PROJECT=your-project-id
  export GCS_BUCKET=your-gcs-bucket       # bucket must already exist
  export SIGNER_SA=<sa-email>             # optional; defaults to appspot SA
  export BLOB_NAME=data/sales_data.csv    # optional; object to sign
"""

import os

from google.cloud import storage

from signed_urls import generate_signed_url, PROJECT_ID, GCS_BUCKET
from lifecycle import (
    print_lifecycle_rules,
    clear_lifecycle_rules,
    apply_standard_lifecycle_rules,
)

BLOB_NAME = os.environ.get("BLOB_NAME", "data/sales_data.csv")
# Set CLEAR_RULES_AFTER=true to remove demo rules after the run
CLEAR_RULES_AFTER = os.environ.get("CLEAR_RULES_AFTER", "false").lower() == "true"


def main():
    print("=" * 60)
    print("  Project 09: GCS Signed URLs + Lifecycle Rules")
    print("=" * 60)
    print(f"\nProject  : {PROJECT_ID}")
    print(f"Bucket   : gs://{GCS_BUCKET}")
    print(f"Object   : {BLOB_NAME}")

    # Step 1: Signed GET URL
    print("\n[1/4] Generating V4 signed GET URL (15 min) ...")
    generate_signed_url(BLOB_NAME, method="GET", expiration_minutes=15)

    # Step 2: Signed PUT URL (pre-signed upload)
    print("\n[2/4] Generating V4 signed PUT URL for upload (60 min) ...")
    generate_signed_url(BLOB_NAME, method="PUT", expiration_minutes=60)

    # Step 3: Current lifecycle rules
    client = storage.Client(project=PROJECT_ID)
    bucket = client.get_bucket(GCS_BUCKET)
    print("\n[3/4] Reading current lifecycle rules ...")
    print_lifecycle_rules(bucket, label="before")

    # Step 4: Apply standard lifecycle rules
    print("\n[4/4] Applying standard lifecycle rules ...")
    apply_standard_lifecycle_rules(bucket)

    # Optional cleanup: remove the demo rules
    if CLEAR_RULES_AFTER:
        print("\nCLEAR_RULES_AFTER=true — removing demo lifecycle rules ...")
        clear_lifecycle_rules(bucket)
        print_lifecycle_rules(bucket, label="after cleanup")
    else:
        print("\nNote: lifecycle rules have been applied to the bucket.")
        print("Set CLEAR_RULES_AFTER=true to auto-remove them after the demo.")

    print("\nDone.")


if __name__ == "__main__":
    main()
