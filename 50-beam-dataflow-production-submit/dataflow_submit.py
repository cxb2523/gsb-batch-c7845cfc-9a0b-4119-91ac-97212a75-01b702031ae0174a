# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Submit a Beam Pipeline to the Dataflow Service

The CSV → revenue-per-region pipeline parameterized for a REAL Dataflow run:
reads from GCS, writes results to GCS. Gated by DRY_RUN (default True) so
nothing is billed until you explicitly opt in.

Key differences vs DirectRunner samples:
  - All I/O must be on GCS (local paths don't exist on Dataflow workers)
  - PipelineOptions carries project, region, temp_location, job_name
  - pipeline.run() returns after job SUBMISSION — job keeps running on GCP
  - Cost controls: max_num_workers caps autoscaling; machine_type sets worker size

Cost warning:
  Dataflow bills worker VMs (vCPU + RAM + disk per second) from job startup
  to teardown. Even a tiny dataset runs for several minutes minimum — a few
  cents per run, but REAL money. A forgotten streaming job is much worse.
  DirectRunner runs the identical graph locally for free (projects 47-49).
"""

import csv
import io
import os
import time

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")

# DRY_RUN=True by default — no Dataflow job is submitted, no cost incurred.
# Set DRY_RUN=false to actually submit.
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"

CSV_COLUMNS = [
    "order_id", "order_date", "customer_name", "region", "product",
    "category", "quantity", "unit_price", "total_amount",
]


def get_gcs_paths(gcs_bucket: str) -> dict[str, str]:
    """Return the GCS paths for input, output, and temp staging."""
    return {
        "input": f"gs://{gcs_bucket}/data/sales_data.csv",
        "output": f"gs://{gcs_bucket}/output/revenue_by_region",
        "temp": f"gs://{gcs_bucket}/tmp/dataflow/",
    }


def build_pipeline_options(
    project_id: str,
    region: str,
    gcs_bucket: str,
    job_name: str,
) -> PipelineOptions:
    """Build DataflowRunner PipelineOptions with conservative cost controls."""
    paths = get_gcs_paths(gcs_bucket)
    return PipelineOptions(
        runner="DataflowRunner",
        project=project_id,
        region=region,
        temp_location=paths["temp"],
        job_name=job_name,
        # Cost controls — always set on demo jobs:
        max_num_workers=2,             # cap autoscaling blast radius
        machine_type="e2-standard-2",  # smallest sensible worker for small jobs
    )


def parse_csv_line(line: str) -> dict:
    """Parse one CSV line into a typed dict."""
    row = next(csv.reader(io.StringIO(line)))  # quoted-field safe
    record = dict(zip(CSV_COLUMNS, row))
    record["total_amount"] = float(record["total_amount"])
    return record


def build_pipeline_graph(pipeline: beam.Pipeline, input_gcs: str, output_gcs: str) -> None:
    """Attach the revenue-per-region transform graph to the pipeline.

    This is the SAME graph as in project 47 (csv_transform.py) — only the
    I/O endpoints differ (GCS URIs instead of local paths). That portability
    is the entire point of the Beam programming model.
    """
    (
        pipeline
        | "ReadCSVFromGCS" >> beam.io.ReadFromText(input_gcs, skip_header_lines=1)
        | "ParseRows" >> beam.Map(parse_csv_line)
        | "KeyByRegion" >> beam.Map(lambda r: (r["region"], r["total_amount"]))
        | "SumRevenuePerRegion" >> beam.CombinePerKey(sum)
        | "FormatLine" >> beam.MapTuple(
            lambda region, rev: f"{region},{rev:.2f}"
        )
        | "WriteToGCS" >> beam.io.WriteToText(output_gcs, file_name_suffix=".txt")
    )


def run_dataflow_job(
    project_id: str = PROJECT_ID,
    region: str = REGION,
    gcs_bucket: str = GCS_BUCKET,
    dry_run: bool = DRY_RUN,
) -> dict:
    """Submit the Beam pipeline to Dataflow (or simulate if dry_run=True).

    Returns a dict with job metadata: job_name, job_id (if submitted), gcs_paths.
    """
    job_name = f"revenue-by-region-{int(time.time())}"
    paths = get_gcs_paths(gcs_bucket)

    print("=" * 70)
    print("Dataflow job submission — CSV → revenue per region")
    print("=" * 70)
    print(f"  job_name      : {job_name}")
    print(f"  project       : {project_id}")
    print(f"  region        : {region}")
    print(f"  gcs_bucket    : {gcs_bucket}")
    print(f"  input         : {paths['input']}")
    print(f"  output        : {paths['output']}-*.txt")
    print(f"  temp          : {paths['temp']}")
    print(f"  max_workers   : 2")
    print(f"  machine_type  : e2-standard-2")
    print(f"  dry_run       : {dry_run}")

    if dry_run:
        _print_dry_run_message()
        return {
            "job_name": job_name,
            "job_id": None,
            "dry_run": True,
            "gcs_paths": paths,
        }

    # --- Real submission ---
    print("\nDRY_RUN=false — submitting to Dataflow...")
    options = build_pipeline_options(project_id, region, gcs_bucket, job_name)
    pipeline = beam.Pipeline(options=options)
    build_pipeline_graph(pipeline, paths["input"], paths["output"])

    # run() returns immediately after submission with DataflowRunner.
    # The job continues running on GCP. Add result.wait_until_finish() to block.
    result = pipeline.run()
    job_id = getattr(result, "job_id", lambda: "unknown")()

    print(f"\nSubmitted Dataflow job: {job_name}")
    print(f"Job ID  : {job_id}")
    print(
        f"Console : https://console.cloud.google.com/dataflow/jobs/"
        f"{region}/{job_id}?project={project_id}"
    )
    print(f"Output  : {paths['output']}-*.txt (after the job finishes)")
    print(f"\nMonitor: gcloud dataflow jobs list --project={project_id} --region={region}")

    return {
        "job_name": job_name,
        "job_id": job_id,
        "dry_run": False,
        "gcs_paths": paths,
    }


def _print_dry_run_message() -> None:
    print(
        "\nDRY_RUN=true — NOT submitting. No Dataflow job created, no cost incurred.\n"
        "\nCOST WARNING: a Dataflow job bills worker VMs (vCPU + RAM + disk per\n"
        "second) from startup to teardown — minimum ~1 worker for several minutes\n"
        "even for this tiny dataset (~a few cents per run, but real money).\n"
        "A forgotten streaming job is far worse — cancels must be explicit.\n"
        "\nDirectRunner runs the IDENTICAL pipeline graph locally for free:\n"
        "  python -c \"from csv_transform import run_csv_transform; run_csv_transform()\"\n"
        "  (see project 47 for the full local version)\n"
        "\nTo actually submit to Dataflow:\n"
        "  1. Run deploy.sh to create GCS bucket + upload data\n"
        "  2. DRY_RUN=false python main.py"
    )


if __name__ == "__main__":
    run_dataflow_job()
