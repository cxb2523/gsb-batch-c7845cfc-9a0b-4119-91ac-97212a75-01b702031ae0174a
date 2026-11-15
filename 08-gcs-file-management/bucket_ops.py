# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Bucket creation and management helpers for Cloud Storage.

Bucket names are globally unique across all GCP projects — always add a
project-scoped prefix (and optionally a random suffix) to avoid clashes.
Uniform bucket-level access is the recommended IAM model: it disables
per-object ACLs and enforces IAM-only access control consistently.

FinOps note: each empty bucket has zero storage cost, but clutter has a
real ops/audit cost — always clean up demo buckets after use.
"""

import os
import uuid

from google.cloud import storage

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")


def create_demo_bucket(client: storage.Client, location: str = "us-central1") -> storage.Bucket:
    """
    Create a new bucket with a unique name, STANDARD class, and uniform access.
    Returns the created Bucket object.
    """
    bucket_name = f"{PROJECT_ID}-demo-{uuid.uuid4().hex[:8]}"
    print(f"Creating bucket: {bucket_name} in {location} ...")

    bucket = storage.Bucket(client, name=bucket_name)
    bucket.storage_class = "STANDARD"
    bucket.iam_configuration.uniform_bucket_level_access_enabled = True
    bucket.labels = {"env": "demo", "team": "techtrapture", "project": "08-gcs-file-mgmt"}

    bucket = client.create_bucket(bucket, location=location)
    print(f"\n--- Bucket created ---")
    print(f"Name           : {bucket.name}")
    print(f"Location       : {bucket.location}")
    print(f"Storage class  : {bucket.storage_class}")
    print(f"Uniform access : {bucket.iam_configuration.uniform_bucket_level_access_enabled}")
    print(f"Labels         : {bucket.labels}")
    print(f"Self link      : gs://{bucket.name}")
    return bucket


def delete_bucket(bucket: storage.Bucket) -> None:
    """Delete a bucket and all its objects (force=True). For demo cleanup."""
    bucket.delete(force=True)
    print(f"Deleted bucket : gs://{bucket.name}")
