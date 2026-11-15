# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
In-memory streaming: DataFrame <-> GCS via BytesIO.

Zero local disk I/O — the correct pattern for Cloud Run / Cloud Functions
where the filesystem is ephemeral (or mounted as read-only tmpfs).
upload_from_file() with a BytesIO buffer avoids ever writing to disk.

Round-trip integrity is verified with an assert — ensures the CSV encoding/
decoding cycle produces an identical DataFrame.
"""

import os
from io import BytesIO

import pandas as pd
from google.cloud import storage

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")

BLOB_NAME = "demo/stream_metrics.csv"


def upload_dataframe_to_gcs(
    bucket: storage.Bucket,
    df: pd.DataFrame,
    blob_name: str,
) -> storage.Blob:
    """
    Serialize a DataFrame to CSV in-memory and upload to GCS.
    No temp file, no disk write — fully in-process.
    """
    buffer = BytesIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)  # rewind before upload

    blob = bucket.blob(blob_name)
    blob.upload_from_file(buffer, content_type="text/csv")
    print(f"\n--- Upload (DataFrame -> BytesIO -> GCS) ---")
    print(f"Blob    : gs://{bucket.name}/{blob_name}")
    print(f"Rows    : {len(df)}")
    print(f"Columns : {list(df.columns)}")
    return blob


def download_gcs_to_dataframe(blob: storage.Blob) -> pd.DataFrame:
    """
    Download a GCS blob as bytes and parse directly into a DataFrame.
    No temp file written — all in-memory via BytesIO.
    """
    data = blob.download_as_bytes()
    df = pd.read_csv(BytesIO(data))
    print(f"\n--- Download (GCS -> BytesIO -> DataFrame) ---")
    print(df.to_string(index=False))
    return df


def run_stream_demo(client: storage.Client) -> None:
    """Full upload-download-verify round-trip demo using in-memory streaming."""
    bucket = client.bucket(GCS_BUCKET)

    df_original = pd.DataFrame({
        "service": ["cloud-run", "bigquery", "gcs", "vertex-ai", "pub-sub"],
        "region": ["us-central1"] * 5,
        "monthly_cost_usd": [42.50, 118.20, 7.85, 230.00, 15.40],
    })

    blob = upload_dataframe_to_gcs(bucket, df_original, BLOB_NAME)
    df_downloaded = download_gcs_to_dataframe(blob)

    total = df_downloaded["monthly_cost_usd"].sum()
    print(f"\nTotal monthly cost : ${total:.2f}")

    assert df_downloaded.equals(df_original), "Round-trip mismatch!"
    print("Round-trip check   : OK (downloaded frame equals original)")

    blob.delete()
    print(f"Cleanup            : deleted gs://{GCS_BUCKET}/{BLOB_NAME}")
