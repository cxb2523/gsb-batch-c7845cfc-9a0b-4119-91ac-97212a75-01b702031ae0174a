# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Orchestrator: GCS File Management Full Lifecycle Demo

Flow:
  1.  Create a new GCS bucket (unique name, uniform access).
  2.  Upload data/sales_data.csv from disk.
  3.  Upload in-memory string content.
  4.  List all objects (flat) and folder-style (with delimiter).
  5.  Get and set object metadata.
  6.  Copy a blob within the bucket.
  7.  Rename a blob (copy + delete).
  8.  Download blob to a local temp file.
  9.  Download blob as text and preview.
  10. Delete a single blob.
  11. Bulk-delete remaining objects by prefix.
  12. Delete the demo bucket (cleanup).

Set env vars before running:
  export GOOGLE_CLOUD_PROJECT=your-project-id
"""

import os
from pathlib import Path

from google.cloud import storage

from bucket_ops import create_demo_bucket, delete_bucket
from object_ops import (
    upload_file, upload_string,
    list_objects,
    get_metadata, set_metadata,
    copy_blob, rename_blob,
    download_to_file, download_as_text,
    delete_single, delete_by_prefix,
)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCAL_CSV = Path(__file__).parent / "data" / "sales_data.csv"
DEMO_PREFIX = "demo/"


def main():
    print("=" * 60)
    print("  Project 08: GCS File Management Full Lifecycle")
    print("=" * 60)

    client = storage.Client(project=PROJECT_ID)

    # Step 1: Create bucket
    print("\n[1/12] Creating demo bucket ...")
    bucket = create_demo_bucket(client)

    try:
        # Step 2: Upload from disk
        print("\n[2/12] Uploading sales_data.csv from disk ...")
        csv_blob = upload_file(bucket, str(LOCAL_CSV), f"{DEMO_PREFIX}sales_data.csv")

        # Step 3: Upload in-memory content
        print("\n[3/12] Uploading inline cost report (in-memory) ...")
        inline_content = "service,monthly_cost_usd\ncloud-run,42.50\nbigquery,118.20\ngcs,7.85\n"
        cost_blob = upload_string(bucket, inline_content,
                                  f"{DEMO_PREFIX}cost_report.csv", "text/csv")

        # Step 4: List objects
        print("\n[4/12] Listing all objects under demo/ ...")
        list_objects(client, bucket.name, prefix=DEMO_PREFIX)

        print("\n       Folder-style listing with delimiter='/' ...")
        list_objects(client, bucket.name, prefix="", delimiter="/")

        # Step 5: Get + set metadata
        print("\n[5/12] Getting metadata for sales_data.csv ...")
        get_metadata(csv_blob)

        print("\n       Setting custom metadata ...")
        set_metadata(
            csv_blob,
            content_type="text/csv",
            cache_control="public, max-age=3600",
            custom={"owner": "techtrapture", "pipeline": "demo", "pii": "false"},
        )

        # Step 6: Copy
        print("\n[6/12] Copying sales_data.csv -> archive/sales_data_copy.csv ...")
        copied_blob = copy_blob(bucket, csv_blob, f"archive/sales_data_copy.csv")

        # Step 7: Rename (copy + delete)
        print("\n[7/12] Renaming cost_report.csv -> archive/cost_report_final.csv ...")
        renamed_blob = rename_blob(bucket, cost_blob, "archive/cost_report_final.csv")

        # Step 8: Download to file
        print("\n[8/12] Downloading sales_data.csv to local temp file ...")
        local_path = download_to_file(csv_blob)

        # Step 9: Download as text
        print("\n[9/12] Downloading sales_data.csv as text ...")
        download_as_text(csv_blob, preview_lines=3)

        # Cleanup local temp
        local_path.unlink(missing_ok=True)
        print(f"       Cleaned up temp file: {local_path.name}")

        # Step 10: Delete single blob
        print("\n[10/12] Deleting copied blob ...")
        delete_single(copied_blob)

        # Step 11: Bulk delete by prefix
        print(f"\n[11/12] Bulk-deleting remaining objects ...")
        delete_by_prefix(client, bucket.name, prefix="")

    finally:
        # Step 12: Delete bucket regardless of errors above
        print(f"\n[12/12] Deleting demo bucket ...")
        delete_bucket(bucket)

    print("\nDone — full GCS object lifecycle demonstrated.")


if __name__ == "__main__":
    main()
