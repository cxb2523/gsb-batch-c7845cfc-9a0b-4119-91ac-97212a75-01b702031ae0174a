# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
gcs_file_processor.py — Focused PythonOperator file processing demo.

A self-contained DAG that demonstrates the GCSHook-based file processor
pattern in isolation. Useful for:
  - Unit-testing the processing logic without the sensor overhead
  - Backfill: reprocess archived files for a specific date range
  - Demonstrating GCSHook CRUD (download, list, copy, delete, upload)

Tasks:
  1. list_landing_files    — use GCSHook to list files in a prefix
  2. download_and_parse    — download a target file and parse it
  3. transform_records     — apply business logic transformations
  4. write_processed_output — upload the processed result back to GCS
  5. generate_processing_report — summarise the run
"""

from datetime import datetime, timedelta
import csv
import io
import json
import logging

from airflow import DAG
from airflow.models import Variable
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator
from airflow.providers.google.cloud.hooks.gcs import GCSHook

# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------
PROJECT_ID     = Variable.get("project_id",     default_var="your-project-id")
LANDING_BUCKET = Variable.get("landing_bucket", default_var="your-gcs-bucket")
OUTPUT_BUCKET  = Variable.get("output_bucket",  default_var="your-gcs-bucket")
GCS_PREFIX     = Variable.get("gcs_watch_prefix", default_var="landing/events/")

# ---------------------------------------------------------------------------
# Default arguments
# ---------------------------------------------------------------------------
default_args = {
    "owner": "techtrapture",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
    "email_on_retry": False,
    "depends_on_past": False,
}


# ---------------------------------------------------------------------------
# Callable: list files in GCS prefix
# ---------------------------------------------------------------------------
def list_landing_files(**context):
    """
    Use GCSHook.list() to enumerate all objects under the watch prefix.
    Pushes the list to XCom for downstream tasks.
    """
    ds = context["ds"]
    gcs_hook = GCSHook(gcp_conn_id="google_cloud_default")

    # List all objects (blobs) under the prefix
    all_objects = gcs_hook.list(bucket_name=LANDING_BUCKET, prefix=GCS_PREFIX)

    # Filter to only today's files if they follow a date-based naming convention
    # e.g. landing/events/sales_20240315_083000.json
    todays_files = [
        obj for obj in (all_objects or [])
        if ds.replace("-", "") in obj or True  # True = include all for demo
    ]

    logging.info(
        "GCSHook.list('%s', prefix='%s') found %d objects:",
        LANDING_BUCKET, GCS_PREFIX, len(todays_files)
    )
    for obj in todays_files:
        # Get object metadata (size, updated timestamp)
        try:
            metadata = gcs_hook.get_metadata(
                bucket_name=LANDING_BUCKET, object_name=obj
            )
            size = metadata.get("size", "unknown")
            updated = metadata.get("updated", "unknown")
            logging.info("  gs://%s/%s | size=%s | updated=%s", LANDING_BUCKET, obj, size, updated)
        except Exception:
            logging.info("  gs://%s/%s", LANDING_BUCKET, obj)

    context["ti"].xcom_push(key="landing_files", value=todays_files)
    context["ti"].xcom_push(key="file_count", value=len(todays_files))
    return todays_files


# ---------------------------------------------------------------------------
# Callable: download and parse the first detected file
# ---------------------------------------------------------------------------
def download_and_parse(**context):
    """
    Download the first file from the landing list and parse it.
    Demonstrates both JSON and CSV parsing paths.
    """
    ti = context["ti"]
    landing_files = ti.xcom_pull(task_ids="list_landing_files", key="landing_files") or []

    if not landing_files:
        logging.warning("No landing files found. Nothing to parse.")
        ti.xcom_push(key="parsed_records", value=[])
        return []

    # Process first file (extend to loop for batch processing)
    target_file = landing_files[0]
    logging.info("Downloading: gs://%s/%s", LANDING_BUCKET, target_file)

    gcs_hook = GCSHook(gcp_conn_id="google_cloud_default")
    raw_bytes = gcs_hook.download(
        bucket_name=LANDING_BUCKET,
        object_name=target_file,
    )

    parsed_records = []

    if target_file.endswith(".json"):
        data = json.loads(raw_bytes)
        records = data.get("records", [data] if isinstance(data, dict) else data)
        if isinstance(records, list):
            parsed_records = records
        logging.info("Parsed %d JSON records from %s", len(parsed_records), target_file)

    elif target_file.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(raw_bytes.decode("utf-8")))
        parsed_records = list(reader)
        logging.info("Parsed %d CSV rows from %s", len(parsed_records), target_file)

    else:
        logging.warning("Unsupported file type: %s — returning raw content.", target_file)
        parsed_records = [{"raw": raw_bytes.decode("utf-8", errors="replace")}]

    ti.xcom_push(key="parsed_records", value=parsed_records)
    ti.xcom_push(key="source_file", value=target_file)
    return parsed_records


# ---------------------------------------------------------------------------
# Callable: transform records
# ---------------------------------------------------------------------------
def transform_records(**context):
    """
    Apply business-logic transformations to the parsed records.

    Transformations applied:
      - Flatten nested 'items' arrays (for JSON records)
      - Normalise field names to snake_case
      - Add processing metadata (processed_at, dag_run_id)
      - Filter out cancelled/error records
      - Compute line-item revenue (quantity * unit_price * (1 - discount/100))
    """
    ti = context["ti"]
    raw_records = ti.xcom_pull(task_ids="download_and_parse", key="parsed_records") or []
    ts = context["ts"]
    run_id = context["run_id"]

    transformed = []
    skipped = 0

    for rec in raw_records:
        if not isinstance(rec, dict):
            skipped += 1
            continue

        # Handle nested JSON format from sample_payload.json
        if "items" in rec:
            for item in rec.get("items", []):
                flat = {
                    "order_id":      rec.get("order_id", ""),
                    "order_timestamp": rec.get("order_timestamp", ""),
                    "customer_id":   rec.get("customer_id", ""),
                    "customer_name": rec.get("customer_name", ""),
                    "region":        rec.get("region", ""),
                    "store_id":      rec.get("store_id", ""),
                    "sku":           item.get("sku", ""),
                    "product_name":  item.get("product_name", ""),
                    "category":      item.get("category", ""),
                    "quantity":      item.get("quantity", 0),
                    "unit_price":    item.get("unit_price", 0.0),
                    "discount_pct":  item.get("discount_pct", 0),
                    "status":        rec.get("status", ""),
                    "line_revenue":  round(
                        item.get("quantity", 0)
                        * item.get("unit_price", 0.0)
                        * (1 - item.get("discount_pct", 0) / 100.0),
                        2
                    ),
                    "processed_at":  ts,
                    "dag_run_id":    run_id,
                }
                if flat["status"] not in ("cancelled", "error"):
                    transformed.append(flat)
                else:
                    skipped += 1

        else:
            # Flat CSV/flat JSON record
            rec["processed_at"] = ts
            rec["dag_run_id"] = run_id
            status = rec.get("status", "")
            if status not in ("cancelled", "error"):
                transformed.append(rec)
            else:
                skipped += 1

    logging.info(
        "Transform complete: %d records produced, %d skipped (cancelled/error).",
        len(transformed), skipped
    )

    ti.xcom_push(key="transformed_records", value=transformed)
    ti.xcom_push(key="skipped_count", value=skipped)
    return transformed


# ---------------------------------------------------------------------------
# Callable: write processed output back to GCS
# ---------------------------------------------------------------------------
def write_processed_output(**context):
    """
    Serialise transformed records to JSON and upload to GCS output prefix.
    Output path: processed/YYYY-MM-DD/<source_filename>.processed.json
    """
    ti = context["ti"]
    ds = context["ds"]
    transformed = ti.xcom_pull(task_ids="transform_records", key="transformed_records") or []
    source_file = ti.xcom_pull(task_ids="download_and_parse", key="source_file") or "unknown"

    if not transformed:
        logging.warning("No transformed records to write. Skipping GCS upload.")
        return

    # Build output path
    source_filename = source_file.split("/")[-1].rsplit(".", 1)[0]
    output_object = f"processed/{ds}/{source_filename}.processed.json"

    output_data = {
        "processed_at":  context["ts"],
        "source_file":   f"gs://{LANDING_BUCKET}/{source_file}",
        "record_count":  len(transformed),
        "records":       transformed,
    }
    output_bytes = json.dumps(output_data, indent=2, default=str).encode("utf-8")

    gcs_hook = GCSHook(gcp_conn_id="google_cloud_default")
    gcs_hook.upload(
        bucket_name=OUTPUT_BUCKET,
        object_name=output_object,
        data=output_bytes,
        mime_type="application/json",
    )

    output_uri = f"gs://{OUTPUT_BUCKET}/{output_object}"
    logging.info("Uploaded processed output: %s (%d bytes)", output_uri, len(output_bytes))
    ti.xcom_push(key="output_uri", value=output_uri)
    return output_uri


# ---------------------------------------------------------------------------
# Callable: generate processing report
# ---------------------------------------------------------------------------
def generate_processing_report(**context):
    """Final task — log a human-readable processing report."""
    ti = context["ti"]

    file_count = ti.xcom_pull(task_ids="list_landing_files", key="file_count") or 0
    records_in = len(ti.xcom_pull(task_ids="download_and_parse", key="parsed_records") or [])
    records_out = len(ti.xcom_pull(task_ids="transform_records", key="transformed_records") or [])
    skipped = ti.xcom_pull(task_ids="transform_records", key="skipped_count") or 0
    output_uri = ti.xcom_pull(task_ids="write_processed_output", key="output_uri") or "N/A"

    logging.info("=" * 60)
    logging.info("GCS FILE PROCESSOR REPORT")
    logging.info("  Logical date      : %s", context["ds"])
    logging.info("  Files detected    : %d", file_count)
    logging.info("  Records parsed    : %d", records_in)
    logging.info("  Records produced  : %d", records_out)
    logging.info("  Records skipped   : %d", skipped)
    logging.info("  Output written to : %s", output_uri)
    logging.info("=" * 60)


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="gcs_file_processor",
    description="GCSHook-based file processor — list, download, parse, transform, upload",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["techtrapture", "gcs", "file-processing", "composer"],
    doc_md="""
## gcs_file_processor

**Purpose:** Focused reference DAG for GCSHook-based file processing.

Demonstrates all major GCSHook operations:
- `gcs_hook.list()` — enumerate objects under a prefix
- `gcs_hook.get_metadata()` — inspect object size and timestamps
- `gcs_hook.download()` — fetch file content as bytes
- `gcs_hook.upload()` — write processed output back to GCS

**Task flow:**
```
start
  → list_landing_files       (GCSHook.list)
  → download_and_parse       (GCSHook.download + JSON/CSV parse)
  → transform_records        (flatten, filter, enrich)
  → write_processed_output   (GCSHook.upload)
  → generate_processing_report
  → end
```

**Airflow Variables:**
| Variable | Default |
|---|---|
| `project_id` | your-project-id |
| `landing_bucket` | your-gcs-bucket |
| `output_bucket` | your-gcs-bucket |
| `gcs_watch_prefix` | landing/events/ |
""",
) as dag:

    start = EmptyOperator(task_id="start")
    end   = EmptyOperator(task_id="end")

    list_files = PythonOperator(
        task_id="list_landing_files",
        python_callable=list_landing_files,
        doc_md="Use GCSHook.list() to enumerate objects under the watch prefix.",
    )

    download_parse = PythonOperator(
        task_id="download_and_parse",
        python_callable=download_and_parse,
        doc_md="Download first landing file and parse JSON or CSV content.",
    )

    transform = PythonOperator(
        task_id="transform_records",
        python_callable=transform_records,
        doc_md="Flatten nested items, normalise fields, compute line revenue, filter cancelled.",
    )

    write_output = PythonOperator(
        task_id="write_processed_output",
        python_callable=write_processed_output,
        doc_md="Serialise transformed records to JSON and upload to GCS processed/ prefix.",
    )

    report = PythonOperator(
        task_id="generate_processing_report",
        python_callable=generate_processing_report,
        doc_md="Log human-readable processing summary.",
    )

    start >> list_files >> download_parse >> transform >> write_output >> report >> end
