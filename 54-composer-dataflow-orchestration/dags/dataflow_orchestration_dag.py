# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
dataflow_orchestration_dag.py — Full Airflow DAG orchestrating a Dataflow Flex Template job.

Pipeline:
  1. PythonOperator            — pre-flight checks (config validation, DRY_RUN support)
  2. DataflowStartFlexTemplateOperator — launch Flex Template job
  3. DataflowJobStatusSensor   — poll until job reaches terminal state
  4. PythonOperator            — evaluate job outcome (success / failure)
  5. BigQueryInsertJobOperator — post-job BQ validation query
  6. BigQueryCheckOperator     — assert output row count meets threshold
  7. PubSubPublishMessageOperator — completion notification
  8. PythonOperator            — structured final log

Key patterns demonstrated:
  - DRY_RUN mode: validate params without launching job (controlled by Airflow Variable)
  - Sensor with mode="reschedule" and configurable poke_interval
  - XCom: Dataflow job_id flows from launcher → sensor → validator
  - BranchPythonOperator routing based on Dataflow job outcome
  - Post-job BigQuery validation before marking the pipeline successful
"""

from datetime import datetime, timedelta
import json
import logging

from airflow import DAG
from airflow.models import Variable
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator, BranchPythonOperator
from airflow.utils.trigger_rule import TriggerRule
from airflow.providers.google.cloud.operators.bigquery import (
    BigQueryInsertJobOperator,
    BigQueryCheckOperator,
)
from airflow.providers.google.cloud.operators.dataflow import (
    DataflowStartFlexTemplateOperator,
)
from airflow.providers.google.cloud.sensors.dataflow import (
    DataflowJobStatusSensor,
)
from airflow.providers.google.cloud.operators.pubsub import (
    PubSubPublishMessageOperator,
)

# ---------------------------------------------------------------------------
# Runtime config — set as Airflow Variables before triggering
# ---------------------------------------------------------------------------
PROJECT_ID       = Variable.get("project_id",        default_var="your-project-id")
REGION           = Variable.get("gcp_region",         default_var="us-central1")
GCS_BUCKET       = Variable.get("gcs_bucket",         default_var="your-gcs-bucket")
DATASET          = Variable.get("bq_dataset",         default_var="your_dataset")
PUBSUB_TOPIC     = Variable.get("pubsub_topic",       default_var="your-pubsub-topic")
BQ_LOCATION      = Variable.get("bq_location",        default_var="US")

# Dataflow Flex Template location in GCS or Artifact Registry
FLEX_TEMPLATE_PATH = Variable.get(
    "dataflow_flex_template_path",
    default_var=f"gs://your-gcs-bucket/dataflow/templates/sales-etl-template.json",
)

# DRY_RUN: set to "true" to validate parameters without launching the real job
DRY_RUN = Variable.get("dataflow_dry_run", default_var="false").lower() == "true"

# Dataflow job name prefix (actual name gets a date suffix)
JOB_NAME_PREFIX = "sales-etl"

# Dataflow job terminal states recognised as SUCCESS
DATAFLOW_SUCCESS_STATES = {"JOB_STATE_DONE"}
# Terminal failure states
DATAFLOW_FAILURE_STATES = {"JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_DRAINED"}

# ---------------------------------------------------------------------------
# Default arguments
# ---------------------------------------------------------------------------
default_args = {
    "owner": "techtrapture",
    "retries": 1,                           # fewer retries for long Dataflow jobs
    "retry_delay": timedelta(minutes=10),
    "email_on_failure": False,
    "email_on_retry": False,
    "depends_on_past": False,
}


# ---------------------------------------------------------------------------
# Pre-flight check callable
# ---------------------------------------------------------------------------
def preflight_check(**context):
    """
    Validate pipeline configuration before launching the Dataflow job.
    In DRY_RUN mode: log all parameters and exit without launching.
    """
    ds = context["ds"]
    ds_nodash = context["ds_nodash"]
    run_id = context["run_id"]

    config = {
        "project_id":           PROJECT_ID,
        "region":               REGION,
        "gcs_bucket":           GCS_BUCKET,
        "flex_template_path":   FLEX_TEMPLATE_PATH,
        "bq_dataset":           DATASET,
        "logical_date":         ds,
        "job_name":             f"{JOB_NAME_PREFIX}-{ds_nodash}",
        "dry_run":              DRY_RUN,
        "run_id":               run_id,
    }

    logging.info("=" * 60)
    logging.info("PRE-FLIGHT CHECK")
    for k, v in config.items():
        logging.info("  %-28s : %s", k, v)
    logging.info("=" * 60)

    # Validate required config
    errors = []
    if PROJECT_ID == "your-project-id":
        errors.append("project_id Airflow Variable not set")
    if GCS_BUCKET == "your-gcs-bucket":
        errors.append("gcs_bucket Airflow Variable not set")
    if FLEX_TEMPLATE_PATH.startswith("gs://your-gcs-bucket"):
        logging.warning(
            "dataflow_flex_template_path points to placeholder bucket. "
            "Ensure the real Flex Template is built and uploaded."
        )

    if errors:
        for err in errors:
            logging.warning("Config warning: %s", err)

    if DRY_RUN:
        logging.info("DRY_RUN=true — skipping actual Dataflow job launch.")
        logging.info("All parameters validated. Set dataflow_dry_run=false to launch.")

    context["ti"].xcom_push(key="pipeline_config", value=config)
    context["ti"].xcom_push(key="dry_run", value=DRY_RUN)
    context["ti"].xcom_push(key="job_name", value=f"{JOB_NAME_PREFIX}-{ds_nodash}")


# ---------------------------------------------------------------------------
# DRY_RUN branch callable
# ---------------------------------------------------------------------------
def route_after_preflight(**context):
    """Branch: if DRY_RUN, skip Dataflow launch; else proceed normally."""
    is_dry_run = context["ti"].xcom_pull(task_ids="preflight_check", key="dry_run")
    if is_dry_run:
        logging.info("DRY_RUN mode — routing to dry_run_complete.")
        return "dry_run_complete"
    return "launch_dataflow_job"


def log_dry_run_complete(**context):
    """Placeholder task executed only in DRY_RUN mode."""
    config = context["ti"].xcom_pull(task_ids="preflight_check", key="pipeline_config") or {}
    logging.info("DRY_RUN complete. Would have launched job: %s", config.get("job_name"))


# ---------------------------------------------------------------------------
# Post-job outcome evaluation
# ---------------------------------------------------------------------------
def evaluate_dataflow_outcome(**context):
    """
    Check the Dataflow job status from XCom and branch to success/failure path.
    DataflowJobStatusSensor pushes the final job state to XCom.
    """
    ti = context["ti"]

    # DataflowJobStatusSensor pushes the job state to XCom as 'return_value'
    job_state = ti.xcom_pull(task_ids="wait_for_dataflow_job", key="return_value")
    job_id = ti.xcom_pull(task_ids="launch_dataflow_job", key="dataflow_job_id")

    logging.info("Dataflow job ID: %s | Final state: %s", job_id, job_state)
    ti.xcom_push(key="dataflow_job_state", value=job_state)

    if job_state in DATAFLOW_SUCCESS_STATES:
        logging.info("Dataflow job succeeded.")
        return "run_bq_validation"
    else:
        logging.error("Dataflow job did NOT succeed. State: %s", job_state)
        return "handle_dataflow_failure"


def handle_dataflow_failure(**context):
    """Handle Dataflow job failure — log details and raise to mark DAG failed."""
    ti = context["ti"]
    job_state = ti.xcom_pull(task_ids="evaluate_dataflow_outcome", key="dataflow_job_state")
    job_id = ti.xcom_pull(task_ids="launch_dataflow_job", key="dataflow_job_id")

    msg = (
        f"Dataflow job FAILED | "
        f"Job ID: {job_id} | "
        f"State: {job_state} | "
        f"Console: https://console.cloud.google.com/dataflow/jobs/{REGION}/{job_id}"
    )
    logging.error(msg)
    raise RuntimeError(msg)


def log_final_pipeline_summary(**context):
    """Log a structured completion summary for the full pipeline."""
    ti = context["ti"]
    job_id = ti.xcom_pull(task_ids="launch_dataflow_job", key="dataflow_job_id") or "N/A"
    job_state = ti.xcom_pull(task_ids="evaluate_dataflow_outcome", key="dataflow_job_state") or "N/A"
    config = ti.xcom_pull(task_ids="preflight_check", key="pipeline_config") or {}

    logging.info("=" * 60)
    logging.info("DATAFLOW ORCHESTRATION PIPELINE COMPLETE")
    logging.info("  DAG run ID   : %s", context["run_id"])
    logging.info("  Logical date : %s", context["ds"])
    logging.info("  Dataflow job : %s", job_id)
    logging.info("  Final state  : %s", job_state)
    logging.info("  BQ output    : %s.%s.sales_dataflow_output", PROJECT_ID, DATASET)
    logging.info("  Job console  :")
    logging.info("    https://console.cloud.google.com/dataflow/jobs/%s/%s", REGION, job_id)
    logging.info("=" * 60)


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="dataflow_orchestration_dag",
    description="Orchestrate Dataflow Flex Template job: launch → monitor → BQ validate → notify",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["techtrapture", "dataflow", "flex-template", "bigquery", "composer"],
    doc_md="""
## dataflow_orchestration_dag

**Purpose:** Production-grade orchestration of a Dataflow Flex Template job from Airflow.

**Pipeline:**
```
preflight_check
    → branch_dry_run_or_launch
        ├── [DRY_RUN] dry_run_complete → end
        └── [LIVE]    launch_dataflow_job (DataflowStartFlexTemplateOperator)
                          → wait_for_dataflow_job (DataflowJobStatusSensor)
                          → evaluate_dataflow_outcome (BranchPythonOperator)
                              ├── [FAILED] handle_dataflow_failure → end
                              └── [SUCCESS] run_bq_validation
                                               → check_bq_output_rows
                                               → notify_pubsub
                                               → log_final_pipeline_summary
                                               → end
```

**DRY_RUN mode:**
Set Airflow Variable `dataflow_dry_run=true` to validate all parameters
without launching the actual Dataflow job. Useful for CI/CD pipeline validation.

**Airflow Variables:**
| Variable | Default |
|---|---|
| `project_id` | your-project-id |
| `gcp_region` | us-central1 |
| `gcs_bucket` | your-gcs-bucket |
| `bq_dataset` | your_dataset |
| `pubsub_topic` | your-pubsub-topic |
| `dataflow_flex_template_path` | gs://your-gcs-bucket/dataflow/templates/sales-etl-template.json |
| `dataflow_dry_run` | false |
""",
) as dag:

    start = EmptyOperator(task_id="start")
    end   = EmptyOperator(task_id="end", trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)

    # -----------------------------------------------------------------------
    # 1. Pre-flight check
    # -----------------------------------------------------------------------
    preflight = PythonOperator(
        task_id="preflight_check",
        python_callable=preflight_check,
        doc_md="Validate Airflow Variables and pipeline configuration.",
    )

    # -----------------------------------------------------------------------
    # 2. Branch: DRY_RUN or live launch
    # -----------------------------------------------------------------------
    branch_dry_run = BranchPythonOperator(
        task_id="branch_dry_run_or_launch",
        python_callable=route_after_preflight,
        doc_md="Route to dry_run_complete if DRY_RUN=true, else launch Dataflow job.",
    )

    dry_run_complete = PythonOperator(
        task_id="dry_run_complete",
        python_callable=log_dry_run_complete,
        doc_md="DRY_RUN path: log validated config and exit without launching job.",
    )

    # -----------------------------------------------------------------------
    # 3. Launch Dataflow Flex Template job
    # -----------------------------------------------------------------------
    launch_dataflow_job = DataflowStartFlexTemplateOperator(
        task_id="launch_dataflow_job",
        project_id=PROJECT_ID,
        location=REGION,
        body={
            "launchParameter": {
                "jobName":       f"{JOB_NAME_PREFIX}-{{{{ ds_nodash }}}}",
                "containerSpecGcsPath": FLEX_TEMPLATE_PATH,
                "parameters": {
                    # Input: GCS path for today's partition
                    "input_gcs_path": (
                        f"gs://{GCS_BUCKET}/landing/sales/{{{{ ds }}}}/*.json"
                    ),
                    # Output: BigQuery table
                    "output_bq_table": f"{PROJECT_ID}:{DATASET}.sales_dataflow_output",
                    # Temp / staging locations
                    "temp_location":    f"gs://{GCS_BUCKET}/dataflow/temp",
                    "staging_location": f"gs://{GCS_BUCKET}/dataflow/staging",
                },
                "environment": {
                    "tempLocation":         f"gs://{GCS_BUCKET}/dataflow/temp",
                    "stagingLocation":      f"gs://{GCS_BUCKET}/dataflow/staging",
                    "maxWorkers":           4,
                    "numWorkers":           1,
                    "machineType":          "n1-standard-2",
                    "diskSizeGb":           50,
                    # Use a dedicated service account for Dataflow workers
                    "serviceAccountEmail": (
                        f"your-dataflow-sa@{PROJECT_ID}.iam.gserviceaccount.com"
                    ),
                    "additionalExperiments": ["use_runner_v2"],
                },
            }
        },
        # do_xcom_push=True pushes the Dataflow job_id to XCom automatically
        do_xcom_push=True,
        doc_md=(
            "Launch the Dataflow Flex Template job. "
            "Job ID is pushed to XCom as 'dataflow_job_id' for the sensor."
        ),
    )

    # -----------------------------------------------------------------------
    # 4. Sensor — poll Dataflow job until terminal state
    # -----------------------------------------------------------------------
    wait_for_dataflow_job = DataflowJobStatusSensor(
        task_id="wait_for_dataflow_job",
        project_id=PROJECT_ID,
        location=REGION,
        # Pull job_id from DataflowStartFlexTemplateOperator XCom
        job_id="{{ ti.xcom_pull(task_ids='launch_dataflow_job', key='dataflow_job_id') }}",
        expected_statuses=DATAFLOW_SUCCESS_STATES,
        timeout=60 * 60 * 4,    # 4-hour timeout for large jobs
        poke_interval=60,        # poll every 60 seconds
        mode="reschedule",       # release worker slot between polls
        doc_md=(
            "Poll Dataflow job status every 60s (mode=reschedule). "
            "Succeeds when job reaches JOB_STATE_DONE. "
            "Fails task if job reaches a failure state."
        ),
    )

    # -----------------------------------------------------------------------
    # 5. Evaluate job outcome (branch on success vs failure)
    # -----------------------------------------------------------------------
    evaluate_outcome = BranchPythonOperator(
        task_id="evaluate_dataflow_outcome",
        python_callable=evaluate_dataflow_outcome,
        doc_md="Route to BQ validation on success, failure handler on error.",
    )

    handle_failure = PythonOperator(
        task_id="handle_dataflow_failure",
        python_callable=handle_dataflow_failure,
        doc_md="Log Dataflow failure details and raise exception to fail the DAG.",
    )

    # -----------------------------------------------------------------------
    # 6. BQ validation — assert output rows were written
    # -----------------------------------------------------------------------
    run_bq_validation = BigQueryInsertJobOperator(
        task_id="run_bq_validation",
        configuration={
            "query": {
                "query": """
                    -- Compute output stats for the pipeline run
                    SELECT
                        COUNT(*)                          AS total_rows,
                        COUNTIF(order_id IS NULL)        AS null_order_ids,
                        ROUND(SUM(total_amount), 2)      AS total_revenue,
                        MIN(order_date)                  AS min_date,
                        MAX(order_date)                  AS max_date
                    FROM `{{ params.project_id }}.{{ params.dataset }}.sales_dataflow_output`
                    WHERE DATE(_PARTITIONTIME) = DATE('{{ ds }}')
                """,
                "useLegacySql":   False,
                "destinationTable": {
                    "projectId": PROJECT_ID,
                    "datasetId": DATASET,
                    "tableId":   "dataflow_run_stats_{{ ds_nodash }}",
                },
                "createDisposition": "CREATE_IF_NEEDED",
                "writeDisposition":  "WRITE_TRUNCATE",
            }
        },
        project_id=PROJECT_ID,
        location=BQ_LOCATION,
        params={"project_id": PROJECT_ID, "dataset": DATASET},
        doc_md="Run BQ stats query and write results to dataflow_run_stats_YYYYMMDD.",
    )

    check_bq_output_rows = BigQueryCheckOperator(
        task_id="check_bq_output_rows",
        sql="""
            SELECT COUNT(*) > 0
            FROM `{{ params.project_id }}.{{ params.dataset }}.sales_dataflow_output`
            WHERE DATE(_PARTITIONTIME) = DATE('{{ ds }}')
              AND order_id IS NOT NULL
        """,
        use_legacy_sql=False,
        location=BQ_LOCATION,
        params={"project_id": PROJECT_ID, "dataset": DATASET},
        doc_md=(
            "BigQueryCheckOperator: fails if no rows were written by Dataflow for today's "
            "partition, or if all rows have null order_id."
        ),
    )

    # -----------------------------------------------------------------------
    # 7. Publish completion notification
    # -----------------------------------------------------------------------
    notify_completion = PubSubPublishMessageOperator(
        task_id="notify_pubsub_completion",
        project_id=PROJECT_ID,
        topic=PUBSUB_TOPIC,
        messages=[
            {
                "data": (
                    "{{ {"
                    "'event': 'dataflow_pipeline_complete', "
                    "'dag_id': 'dataflow_orchestration_dag', "
                    "'date': ds, "
                    "'job_id': ti.xcom_pull(task_ids='launch_dataflow_job', key='dataflow_job_id'), "
                    "'status': 'SUCCESS'"
                    "} | tojson | b64encode }}"
                ),
                "attributes": {
                    "event_type": "pipeline_complete",
                    "dag_id":     "dataflow_orchestration_dag",
                    "project":    PROJECT_ID,
                    "date":       "{{ ds }}",
                },
            }
        ],
        doc_md="Publish structured completion event to Pub/Sub.",
    )

    # -----------------------------------------------------------------------
    # 8. Final log
    # -----------------------------------------------------------------------
    final_log = PythonOperator(
        task_id="log_final_pipeline_summary",
        python_callable=log_final_pipeline_summary,
        doc_md="Log structured completion summary with Dataflow Console link.",
    )

    # -----------------------------------------------------------------------
    # Wire dependencies
    # -----------------------------------------------------------------------
    start >> preflight >> branch_dry_run

    # DRY_RUN path
    branch_dry_run >> dry_run_complete >> end

    # Live path
    branch_dry_run >> launch_dataflow_job >> wait_for_dataflow_job >> evaluate_outcome

    evaluate_outcome >> handle_failure >> end
    evaluate_outcome >> run_bq_validation >> check_bq_output_rows >> notify_completion >> final_log >> end
