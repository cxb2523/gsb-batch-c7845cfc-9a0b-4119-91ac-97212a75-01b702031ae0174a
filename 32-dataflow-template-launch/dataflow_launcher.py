# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Dataflow Template Launcher — launches a Google-provided classic template via
the Dataflow REST API. The job runs ASYNC on managed workers; this module only
submits and exits.

COST WARNING: Setting DRY_RUN=false submits a REAL paid Dataflow job.
Default template is Word_Count which spins up n1-standard-1 worker VMs billed
per second. Always monitor the console to confirm the job completes.
"""

import os
import time

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")

# DRY_RUN: when True, prints the job parameters without calling the API.
# Set DRY_RUN=false (env var) to submit a real paid Dataflow job.
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"

# Google-provided classic template. The full catalog (GCS_Text_to_BigQuery,
# PubSub_to_BigQuery, JDBC imports, ...) lives under gs://dataflow-templates/.
TEMPLATE_PATH = "gs://dataflow-templates/latest/Word_Count"
INPUT_FILE = "gs://dataflow-samples/shakespeare/kinglear.txt"  # public sample
OUTPUT_PREFIX = f"gs://{BUCKET}/dataflow-output/wordcount/output"
TEMP_LOCATION = f"gs://{BUCKET}/dataflow-output/temp"


def build_job_body(job_name):
    """
    Returns the Dataflow template launch request body dict.

    Args:
        job_name: Unique job name string (names must be unique per project/region).

    Returns:
        dict: request body suitable for templates().launch().
    """
    return {
        "jobName": job_name,
        "parameters": {
            "inputFile": INPUT_FILE,
            "output": OUTPUT_PREFIX,
        },
        "environment": {
            "tempLocation": TEMP_LOCATION,
            # Cap workers — the #1 Dataflow cost control. Autoscaling can
            # otherwise fan out to dozens of VMs on a skewed shuffle.
            "maxWorkers": 2,
        },
    }


def launch_template(
    project_id=PROJECT_ID,
    region=REGION,
    template_path=TEMPLATE_PATH,
    dry_run=DRY_RUN,
):
    """
    Launches the Dataflow classic template job (async).

    Args:
        project_id:    GCP project ID.
        region:        Dataflow region.
        template_path: GCS path to the Dataflow classic template.
        dry_run:       If True, print parameters only — no API call.

    Returns:
        dict: {'job_id': str, 'state': str, 'console_url': str} on success,
              or {'dry_run': True} when dry_run=True.
    """
    job_name = f"gcp-hub-wordcount-{int(time.time())}"
    body = build_job_body(job_name)

    print(f"  template   : {template_path}")
    print(f"  job name   : {job_name}")
    print(f"  region     : {region}")
    print(f"  input      : {INPUT_FILE}")
    print(f"  output     : {OUTPUT_PREFIX}")
    print(f"  temp       : {TEMP_LOCATION}")
    print(f"  max workers: {body['environment']['maxWorkers']}")

    if dry_run:
        print("\n  [DRY RUN] Job NOT submitted. Set DRY_RUN=false to submit a real job.")
        print("  WARNING: A real job incurs Dataflow VM charges (billed per second).")
        return {"dry_run": True}

    credentials, _ = google.auth.default()
    dataflow = build("dataflow", "v1b3", credentials=credentials)

    request = dataflow.projects().locations().templates().launch(
        projectId=project_id,
        location=region,
        gcsPath=template_path,
        body=body,
    )

    print(f"\n  Submitting Dataflow job (this is a REAL paid job)...")
    try:
        response = request.execute()
    except HttpError as e:
        # Typical failures:
        #  403: Dataflow API not enabled -> gcloud services enable dataflow.googleapis.com
        #  403: missing roles/dataflow.admin or worker SA permissions
        #  429/quota: regional CPU or in-use-IP quota exhausted
        print(f"\n  ERROR launching Dataflow job (HTTP {e.resp.status}):")
        print(f"  {e._get_reason()}")
        print(f"  Checklist: API enabled? roles/dataflow.admin granted? "
              f"CPU quota free in {region}?")
        raise SystemExit(1)

    job = response["job"]
    job_id = job["id"]
    console_url = (
        f"https://console.cloud.google.com/dataflow/jobs/{region}/{job_id}"
        f"?project={project_id}"
    )
    print(f"\n  Job submitted (workers start in ~2-3 min):")
    print(f"    job id      : {job_id}")
    print(f"    state       : {job.get('currentState', 'JOB_STATE_PENDING')}")
    print(f"    console URL : {console_url}")
    print(f"\n  Monitor: gcloud dataflow jobs show {job_id} --region={region}")
    return {
        "job_id": job_id,
        "state": job.get("currentState", "JOB_STATE_PENDING"),
        "console_url": console_url,
    }
