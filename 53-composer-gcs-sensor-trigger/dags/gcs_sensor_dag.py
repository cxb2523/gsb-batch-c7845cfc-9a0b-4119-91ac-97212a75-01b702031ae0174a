# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
gcs_sensor_dag.py — Event-driven pipeline triggered by file landing in GCS.

Pipeline:
  1. GCSObjectsWithPrefixExistenceSensor — poll landing bucket for new files
  2. PythonOperator (process_file)       — read, validate, and transform the payload
  3. PythonOperator (archive_file)       — move processed file to archive bucket
  4. PubSubPublishMessageOperator        — publish completion event to Pub/Sub
  5. PythonOperator (log_completion)     — final structured log

Key patterns:
  - mode="reschedule" sensor to avoid holding a worker slot while polling
  - XCom to pass file metadata and processing results between tasks
  - GCS move = copy + delete (archive pattern)
  - Pub/Sub notification for downstream consumers
"""

from datetime import datetime, timedelta
import json
import logging

from airflow import DAG
from airflow.models import Variable
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator
from airflow.providers.google.cloud.sensors.gcs import (
    GCSObjectsWithPrefixExistenceSensor,
)
from airflow.providers.google.cloud.transfers.gcs_to_gcs import GCSToGCSOperator
from airflow.providers.google.cloud.hooks.gcs import GCSHook
from airflow.providers.google.cloud.operators.pubsub import PubSubPublishMessageOperator

# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------
PROJECT_ID      = Variable.get("project_id",       default_var="your-project-id")
LANDING_BUCKET  = Variable.get("landing_bucket",   default_var="your-gcs-bucket")
ARCHIVE_BUCKET  = Variable.get("archive_bucket",   default_var="your-gcs-bucket")
PUBSUB_TOPIC    = Variable.get("pubsub_topic",     default_var="your-pubsub-topic")

# GCS prefix to watch for new files (e.g. "landing/events/")
GCS_PREFIX = Variable.get("gcs_watch_prefix", default_var="landing/events/")

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
# Python callables
# ---------------------------------------------------------------------------

def detect_and_register_files(**context):
    """
    Called after the sensor fires. The sensor pushes the list of matched
    objects to XCom under key 'return_value'. We re-expose it with more
    context to make downstream tasks explicit.
    """
    ti = context["ti"]
    # GCSObjectsWithPrefixExistenceSensor pushes matched object names to XCom
    matched_objects = ti.xcom_pull(
        task_ids="wait_for_landing_files",
        key="return_value",
    ) or []

    logging.info("Sensor detected %d file(s) in gs://%s/%s:",
                 len(matched_objects), LANDING_BUCKET, GCS_PREFIX)
    for obj in matched_objects:
        logging.info("  - gs://%s/%s", LANDING_BUCKET, obj)

    # Push structured file list for the processor
    ti.xcom_push(key="files_to_process", value=matched_objects)
    ti.xcom_push(key="file_count", value=len(matched_objects))
    return matched_objects


def process_file(**context):
    """
    Download and process each detected file from GCS landing bucket.
    Supports JSON and CSV file types.
    Records processing results in XCom.
    """
    ti = context["ti"]
    files_to_process = ti.xcom_pull(
        task_ids="register_detected_files",
        key="files_to_process",
    ) or []

    if not files_to_process:
        logging.warning("No files to process — skipping.")
        ti.xcom_push(key="processed_files", value=[])
        ti.xcom_push(key="total_records", value=0)
        return

    gcs_hook = GCSHook(gcp_conn_id="google_cloud_default")
    processing_results = []
    total_records = 0

    for gcs_object in files_to_process:
        logging.info("Processing: gs://%s/%s", LANDING_BUCKET, gcs_object)

        try:
            # Download file content
            content = gcs_hook.download(
                bucket_name=LANDING_BUCKET,
                object_name=gcs_object,
            )

            file_result = {
                "object": gcs_object,
                "status": "unknown",
                "record_count": 0,
                "errors": [],
            }

            # Detect file type and parse
            if gcs_object.endswith(".json"):
                data = json.loads(content)
                records = data.get("records", [data] if isinstance(data, dict) else data)
                record_count = len(records) if isinstance(records, list) else 1
                batch_summary = data.get("batch_summary", {})

                logging.info(
                    "  JSON file: %d records | batch_summary: %s",
                    record_count, batch_summary
                )

                # Basic validation
                errors = []
                if isinstance(records, list):
                    for i, rec in enumerate(records):
                        if isinstance(rec, dict) and not rec.get("order_id"):
                            errors.append(f"Row {i}: missing order_id")

                file_result.update({
                    "status": "processed" if not errors else "processed_with_warnings",
                    "record_count": record_count,
                    "errors": errors,
                    "batch_summary": batch_summary,
                })
                total_records += record_count

            elif gcs_object.endswith(".csv"):
                import csv, io
                reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
                rows = list(reader)
                record_count = len(rows)

                logging.info("  CSV file: %d rows", record_count)

                errors = []
                for i, row in enumerate(rows):
                    if not row.get("order_id") and not row.get("id"):
                        errors.append(f"Row {i}: missing identifier column")

                file_result.update({
                    "status": "processed" if not errors else "processed_with_warnings",
                    "record_count": record_count,
                    "errors": errors,
                })
                total_records += record_count

            else:
                logging.warning("  Unknown file type — treating as opaque blob.")
                file_result["status"] = "skipped_unknown_type"

        except Exception as exc:
            logging.error("  Error processing %s: %s", gcs_object, exc)
            file_result = {
                "object": gcs_object,
                "status": "error",
                "record_count": 0,
                "errors": [str(exc)],
            }

        processing_results.append(file_result)
        logging.info("  Result: %s", file_result)

    logging.info("Processing complete: %d file(s), %d total records.",
                 len(processing_results), total_records)

    ti.xcom_push(key="processed_files", value=processing_results)
    ti.xcom_push(key="total_records", value=total_records)
    return processing_results


def archive_processed_files(**context):
    """
    Move each processed file from landing bucket to archive bucket.
    Archive path: archive/processed/YYYY-MM-DD/<original_filename>

    Uses GCSHook for copy + delete (GCS has no native move/rename).
    """
    ti = context["ti"]
    ds = context["ds"]
    processed_files = ti.xcom_pull(
        task_ids="process_landing_files",
        key="processed_files",
    ) or []

    if not processed_files:
        logging.info("No files to archive.")
        return

    gcs_hook = GCSHook(gcp_conn_id="google_cloud_default")
    archived = []

    for file_result in processed_files:
        src_object = file_result.get("object", "")
        if not src_object or file_result.get("status") == "error":
            logging.warning("Skipping archive for: %s (status: %s)",
                            src_object, file_result.get("status"))
            continue

        # Build archive destination path
        filename = src_object.split("/")[-1]
        dst_object = f"archive/processed/{ds}/{filename}"

        try:
            # Step 1: Copy to archive
            gcs_hook.copy(
                source_bucket=LANDING_BUCKET,
                source_object=src_object,
                destination_bucket=ARCHIVE_BUCKET,
                destination_object=dst_object,
            )
            logging.info("Copied: gs://%s/%s → gs://%s/%s",
                         LANDING_BUCKET, src_object, ARCHIVE_BUCKET, dst_object)

            # Step 2: Delete from landing (completes the "move")
            gcs_hook.delete(bucket_name=LANDING_BUCKET, object_name=src_object)
            logging.info("Deleted source: gs://%s/%s", LANDING_BUCKET, src_object)

            archived.append({
                "source":      f"gs://{LANDING_BUCKET}/{src_object}",
                "destination": f"gs://{ARCHIVE_BUCKET}/{dst_object}",
                "status":      "archived",
            })

        except Exception as exc:
            logging.error("Archive failed for %s: %s", src_object, exc)
            archived.append({
                "source": f"gs://{LANDING_BUCKET}/{src_object}",
                "status": "archive_error",
                "error":  str(exc),
            })

    logging.info("Archived %d/%d files.", len(archived), len(processed_files))
    ti.xcom_push(key="archived_files", value=archived)


def build_pubsub_message(**context):
    """Build the Pub/Sub notification payload and push to XCom."""
    ti = context["ti"]
    ds = context["ds"]
    run_id = context["run_id"]

    processed_files = ti.xcom_pull(task_ids="process_landing_files", key="processed_files") or []
    total_records = ti.xcom_pull(task_ids="process_landing_files", key="total_records") or 0
    archived_files = ti.xcom_pull(task_ids="archive_processed_files", key="archived_files") or []

    message = {
        "event_type": "pipeline_completion",
        "dag_id":     "gcs_sensor_dag",
        "run_id":     run_id,
        "logical_date": ds,
        "project_id": PROJECT_ID,
        "summary": {
            "files_processed": len(processed_files),
            "total_records":   total_records,
            "files_archived":  len([f for f in archived_files if f.get("status") == "archived"]),
        },
        "files": [
            {
                "object":       f.get("object"),
                "status":       f.get("status"),
                "record_count": f.get("record_count", 0),
            }
            for f in processed_files
        ],
    }

    message_str = json.dumps(message)
    logging.info("Pub/Sub message payload: %s", message_str)

    # PubSubPublishMessageOperator expects messages as list of dicts with 'data' key
    # 'data' must be a bytes-compatible string (will be base64-encoded by the operator)
    ti.xcom_push(key="pubsub_message", value=message_str)
    return message_str


def log_completion(**context):
    """Final task — log a structured pipeline summary."""
    ti = context["ti"]
    processed = ti.xcom_pull(task_ids="process_landing_files", key="processed_files") or []
    total_records = ti.xcom_pull(task_ids="process_landing_files", key="total_records") or 0
    archived = ti.xcom_pull(task_ids="archive_processed_files", key="archived_files") or []

    logging.info("=" * 60)
    logging.info("GCS SENSOR PIPELINE COMPLETE")
    logging.info("  Logical date  : %s", context["ds"])
    logging.info("  Files processed: %d", len(processed))
    logging.info("  Total records  : %d", total_records)
    logging.info("  Files archived : %d", len([a for a in archived if a.get("status") == "archived"]))
    logging.info("  Pub/Sub topic  : %s", PUBSUB_TOPIC)
    logging.info("=" * 60)


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="gcs_sensor_dag",
    description="Event-driven pipeline: GCS file arrival → process → archive → Pub/Sub notify",
    schedule="*/15 * * * *",   # check every 15 minutes
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,          # prevent concurrent runs from racing on the same files
    default_args=default_args,
    tags=["techtrapture", "gcs", "sensor", "event-driven", "pubsub"],
    doc_md="""
## gcs_sensor_dag

**Purpose:** Event-driven pipeline that fires when files land in a GCS bucket prefix.
Runs every 15 minutes; if no new files are found the sensor exits cleanly.

**Pipeline:**
```
GCSObjectsWithPrefixExistenceSensor  (mode=reschedule, polls gs://landing_bucket/landing/events/)
    → PythonOperator: register_detected_files
    → PythonOperator: process_landing_files   (parse JSON/CSV, validate, XCom results)
    → PythonOperator: archive_processed_files (copy landing→archive, delete source)
    → PythonOperator: build_notification_message
    → PubSubPublishMessageOperator             (publish to your-pubsub-topic)
    → PythonOperator: log_completion
```

**Airflow Variables:**
| Variable | Default |
|---|---|
| `project_id` | your-project-id |
| `landing_bucket` | your-gcs-bucket |
| `archive_bucket` | your-gcs-bucket |
| `pubsub_topic` | your-pubsub-topic |
| `gcs_watch_prefix` | landing/events/ |

**To trigger manually:** Drop a file at
`gs://your-gcs-bucket/landing/events/sales_YYYYMMDD_HHMMSS.json`
""",
) as dag:

    start = EmptyOperator(task_id="start")

    # -----------------------------------------------------------------------
    # 1. Sensor — poll for files under the configured prefix
    # -----------------------------------------------------------------------
    wait_for_landing_files = GCSObjectsWithPrefixExistenceSensor(
        task_id="wait_for_landing_files",
        bucket=LANDING_BUCKET,
        prefix=GCS_PREFIX,
        timeout=60 * 14,        # just under the 15-min schedule interval
        poke_interval=60,       # check every 60 seconds
        mode="reschedule",      # release worker slot between pokes
        doc_md=(
            "Wait for at least one object matching "
            f"gs://{LANDING_BUCKET}/{GCS_PREFIX}* to exist."
        ),
    )

    # -----------------------------------------------------------------------
    # 2. Register detected files
    # -----------------------------------------------------------------------
    register_detected_files = PythonOperator(
        task_id="register_detected_files",
        python_callable=detect_and_register_files,
        doc_md="Read XCom from sensor, log detected files, re-push structured list.",
    )

    # -----------------------------------------------------------------------
    # 3. Process each file
    # -----------------------------------------------------------------------
    process_landing_files = PythonOperator(
        task_id="process_landing_files",
        python_callable=process_file,
        doc_md="Download and parse each detected JSON/CSV file; push processing results to XCom.",
    )

    # -----------------------------------------------------------------------
    # 4. Archive processed files (copy → archive bucket, delete from landing)
    # -----------------------------------------------------------------------
    archive_files = PythonOperator(
        task_id="archive_processed_files",
        python_callable=archive_processed_files,
        doc_md="Move processed files from landing to archive/processed/YYYY-MM-DD/.",
    )

    # -----------------------------------------------------------------------
    # 5. Build Pub/Sub notification message
    # -----------------------------------------------------------------------
    build_notification = PythonOperator(
        task_id="build_notification_message",
        python_callable=build_pubsub_message,
        doc_md="Construct JSON Pub/Sub message payload from processing results.",
    )

    # -----------------------------------------------------------------------
    # 6. Publish completion notification to Pub/Sub
    # -----------------------------------------------------------------------
    notify_pubsub = PubSubPublishMessageOperator(
        task_id="notify_pubsub_completion",
        project_id=PROJECT_ID,
        topic=PUBSUB_TOPIC,
        messages=[
            {
                "data": "{{ ti.xcom_pull(task_ids='build_notification_message') | b64encode }}",
                "attributes": {
                    "dag_id":     "gcs_sensor_dag",
                    "event_type": "pipeline_completion",
                    "date":       "{{ ds }}",
                    "project":    PROJECT_ID,
                },
            }
        ],
        doc_md=(
            "Publish pipeline completion notification to Pub/Sub topic. "
            "Downstream consumers (e.g. BigQuery subscriptions, Cloud Functions) "
            "can subscribe to trigger further processing."
        ),
    )

    # -----------------------------------------------------------------------
    # 7. Final log
    # -----------------------------------------------------------------------
    log_completion_task = PythonOperator(
        task_id="log_completion",
        python_callable=log_completion,
        doc_md="Emit structured completion log line.",
    )

    end = EmptyOperator(task_id="end")

    # -----------------------------------------------------------------------
    # Wire dependencies
    # -----------------------------------------------------------------------
    (
        start
        >> wait_for_landing_files
        >> register_detected_files
        >> process_landing_files
        >> archive_files
        >> build_notification
        >> notify_pubsub
        >> log_completion_task
        >> end
    )
