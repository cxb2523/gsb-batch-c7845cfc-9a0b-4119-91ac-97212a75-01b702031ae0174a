# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Dataproc Serverless PySpark Batch Submitter.

Uploads a PySpark word-count script to GCS and submits it as a Dataproc
Serverless batch. No cluster to create or tear down — Serverless handles
all infra provisioning.

COST WARNING: Setting DRY_RUN=false submits a REAL paid Dataproc batch.
Serverless bills per DCU-hour with a ~2 DCU / 1-minute minimum per batch.
Every submission spins real Spark infra — do not loop it.
"""

import os
import time

from google.api_core.exceptions import GoogleAPICallError, PermissionDenied
from google.cloud import dataproc_v1, storage

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")
SCRIPT_BLOB = "dataproc-staging/wordcount_job.py"

# DRY_RUN: when True, prints parameters without calling the Dataproc API.
# Set DRY_RUN=false (env var) to submit a real paid Dataproc batch.
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"

# The PySpark job itself — inline so the project is self-contained.
# Reads a public Shakespeare dataset from GCS, counts word frequencies,
# and writes the result CSV back to your bucket.
# This is the exact pattern for a real lake transform (just at smaller volume).
PYSPARK_SCRIPT = '''\
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.builder.appName("gcp-hub-wordcount").getOrCreate()

df = spark.read.text("gs://dataflow-samples/shakespeare/kinglear.txt")
words = (
    df.select(F.explode(F.split(F.lower(F.col("value")), "\\\\W+")).alias("word"))
      .filter(F.col("word") != "")
      .groupBy("word")
      .count()
      .orderBy(F.desc("count"))
)
words.show(20)
words.coalesce(1).write.mode("overwrite").csv(
    "gs://{bucket}/dataproc-output/wordcount_output"
)
spark.stop()
'''.format(bucket=BUCKET)


def stage_pyspark_script(bucket_name=BUCKET, blob_name=SCRIPT_BLOB, project_id=PROJECT_ID):
    """
    Uploads the inline PySpark script to GCS.
    Dataproc Serverless only accepts GCS URIs for job scripts.

    Args:
        bucket_name: GCS bucket name.
        blob_name:   GCS object path within the bucket.
        project_id:  GCP project ID.

    Returns:
        str: GCS URI of the staged script (gs://bucket/blob).
    """
    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(PYSPARK_SCRIPT, content_type="text/x-python")
    uri = f"gs://{bucket_name}/{blob_name}"
    print(f"  staged script : {uri}")
    return uri


def submit_batch(
    project_id=PROJECT_ID,
    region=REGION,
    script_uri=None,
    dry_run=DRY_RUN,
):
    """
    Submits the PySpark script as a Dataproc Serverless batch (async).

    Why Serverless over a managed cluster:
    - Zero idle cost (clusters bill while parked)
    - Per-job isolation
    - Autoscaling handled automatically
    Use a cluster only for long-lived interactive work (notebooks) or when
    you need pinned custom images / init actions.

    Args:
        project_id: GCP project ID.
        region:     Dataproc Serverless region.
        script_uri: GCS URI of the PySpark script to run.
        dry_run:    If True, print parameters only — no API call.

    Returns:
        dict: {'batch_id': str, 'console_url': str} on success,
              or {'dry_run': True} when dry_run=True.
    """
    batch_id = f"gcp-hub-wordcount-{int(time.time())}"
    console_url = (
        f"https://console.cloud.google.com/dataproc/batches/{region}/{batch_id}"
        f"?project={project_id}"
    )

    print(f"  batch id   : {batch_id}")
    print(f"  region     : {region}")
    print(f"  script     : {script_uri or '(not staged yet)'}")
    print(f"  output     : gs://{BUCKET}/dataproc-output/wordcount_output/")

    if dry_run:
        print("\n  [DRY RUN] Batch NOT submitted. Set DRY_RUN=false to submit a real batch.")
        print("  WARNING: A real batch incurs Dataproc Serverless DCU-hour charges.")
        return {"dry_run": True}

    client = dataproc_v1.BatchControllerClient(
        client_options={"api_endpoint": f"{region}-dataproc.googleapis.com:443"}
    )
    batch = dataproc_v1.Batch(
        pyspark_batch=dataproc_v1.PySparkBatch(main_python_file_uri=script_uri),
        # runtime_config.version pins the Spark runtime; omit for latest LTS.
        # environment_config can pin a custom service account / VPC subnet —
        # required in Shared-VPC setups where the default network is gone.
    )

    print(f"\n  Submitting Dataproc Serverless batch (this is a REAL paid job)...")
    try:
        client.create_batch(
            parent=f"projects/{project_id}/locations/{region}",
            batch=batch,
            batch_id=batch_id,
        )
        # create_batch returns a long-running operation immediately.
        # We do NOT call operation.result() — that would block until Spark
        # finishes (minutes). Submit-and-exit is the pattern for orchestrated jobs.
        print(f"  Batch submitted (async — Spark spins up in ~60s):")
        print(f"    console URL : {console_url}")
        print(f"\n  Monitor: gcloud dataproc batches describe {batch_id} --region={region}")
    except PermissionDenied as e:
        print("\n  ERROR: permission/API problem submitting batch:")
        print(f"  {e.message}")
        print("  Checklist: dataproc.googleapis.com enabled? "
              "roles/dataproc.editor on you, roles/dataproc.worker on "
              "the default compute SA?")
        raise SystemExit(1)
    except GoogleAPICallError as e:
        print(f"\n  ERROR: batch submission failed: {e.message}")
        print("  Common causes: regional quota, missing VPC subnet with "
              "Private Google Access, batch_id collision.")
        raise SystemExit(1)

    return {"batch_id": batch_id, "console_url": console_url}
