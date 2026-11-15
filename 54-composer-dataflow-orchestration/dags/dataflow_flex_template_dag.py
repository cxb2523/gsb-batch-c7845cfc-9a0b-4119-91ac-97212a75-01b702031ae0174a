# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
dataflow_flex_template_dag.py — Focused reference demo of DataflowStartFlexTemplateOperator.

This DAG demonstrates all key parameters and patterns for
DataflowStartFlexTemplateOperator, including:

  - Minimal vs full parameter configurations
  - Custom service account, network, and worker settings
  - Passing pipeline parameters as a dictionary
  - do_xcom_push for downstream job monitoring
  - DataflowJobStatusSensor wiring
  - DataflowJobAutoScalingEventsSensor (shows scaling events)
  - DataflowJobMessagesSensor (shows job log messages)
  - Cleanup via DataflowTemplatedJobStartOperator (Classic Templates reference)

Two sub-DAGs / task groups shown:
  A. minimal_flex_launch  — simplest possible Flex Template invocation
  B. production_flex_launch — full production-grade configuration
"""

from datetime import datetime, timedelta
import logging

from airflow import DAG
from airflow.models import Variable
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator
from airflow.utils.task_group import TaskGroup
from airflow.utils.trigger_rule import TriggerRule
from airflow.providers.google.cloud.operators.dataflow import (
    DataflowStartFlexTemplateOperator,
    DataflowTemplatedJobStartOperator,     # Classic Template (for reference)
)
from airflow.providers.google.cloud.sensors.dataflow import (
    DataflowJobStatusSensor,
    DataflowJobAutoScalingEventsSensor,
    DataflowJobMessagesSensor,
)

# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------
PROJECT_ID = Variable.get("project_id",   default_var="your-project-id")
REGION     = Variable.get("gcp_region",   default_var="us-central1")
GCS_BUCKET = Variable.get("gcs_bucket",   default_var="your-gcs-bucket")
DATASET    = Variable.get("bq_dataset",   default_var="your_dataset")

# Flex Template GCS path (built by deploy.sh)
FLEX_TEMPLATE_PATH = Variable.get(
    "dataflow_flex_template_path",
    default_var=f"gs://your-gcs-bucket/dataflow/templates/sales-etl-template.json",
)

# Classic Template path (for reference task)
CLASSIC_TEMPLATE_PATH = Variable.get(
    "dataflow_classic_template_path",
    default_var=f"gs://dataflow-templates/latest/GCS_Text_to_BigQuery",
)

# ---------------------------------------------------------------------------
# Default arguments
# ---------------------------------------------------------------------------
default_args = {
    "owner": "techtrapture",
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "email_on_failure": False,
    "email_on_retry": False,
    "depends_on_past": False,
}


def log_job_ids(**context):
    """Pull and log all Dataflow job IDs from XCom."""
    ti = context["ti"]
    minimal_job = ti.xcom_pull(task_ids="minimal_flex_launch.launch_minimal_flex_job",
                               key="dataflow_job_id")
    prod_job = ti.xcom_pull(task_ids="production_flex_launch.launch_production_flex_job",
                            key="dataflow_job_id")
    logging.info("Minimal Flex Template job ID : %s", minimal_job)
    logging.info("Production Flex Template job ID: %s", prod_job)
    logging.info(
        "Dataflow Console: https://console.cloud.google.com/dataflow/jobs/%s",
        REGION
    )


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="dataflow_flex_template_dag",
    description="Reference demo of DataflowStartFlexTemplateOperator — minimal vs production config",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["techtrapture", "dataflow", "flex-template", "reference", "composer"],
    doc_md="""
## dataflow_flex_template_dag

**Purpose:** Focused reference for `DataflowStartFlexTemplateOperator` showing
minimal and production-grade configurations side by side.

**Task groups:**

| Group | Description |
|---|---|
| `minimal_flex_launch` | Simplest possible Flex Template invocation |
| `production_flex_launch` | Full config: SA, network, autoscaling, sensors |

**Key parameters demonstrated:**
- `containerSpecGcsPath` — GCS path to the Flex Template spec JSON
- `parameters` — pipeline parameters dictionary (template-specific)
- `environment` — Dataflow runner environment (workers, SA, network)
- `do_xcom_push=True` — enables downstream job ID access via XCom
- `DataflowJobStatusSensor` — poll for terminal state
- `DataflowJobAutoScalingEventsSensor` — observe autoscaling events
- `DataflowJobMessagesSensor` — stream job log messages into Airflow logs

**Classic Template reference:**
The `classic_template_reference` task shows `DataflowTemplatedJobStartOperator`
for older Classic Template syntax, for teams migrating to Flex Templates.

**Airflow Variables:**
| Variable | Default |
|---|---|
| `project_id` | your-project-id |
| `gcp_region` | us-central1 |
| `gcs_bucket` | your-gcs-bucket |
| `bq_dataset` | your_dataset |
| `dataflow_flex_template_path` | gs://your-gcs-bucket/.../sales-etl-template.json |
""",
) as dag:

    start = EmptyOperator(task_id="start")
    end   = EmptyOperator(
        task_id="end",
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
    )

    # -----------------------------------------------------------------------
    # Group A: Minimal Flex Template launch
    # -----------------------------------------------------------------------
    with TaskGroup(
        group_id="minimal_flex_launch",
        tooltip="Minimal DataflowStartFlexTemplateOperator — required params only"
    ) as minimal_group:

        launch_minimal_flex_job = DataflowStartFlexTemplateOperator(
            task_id="launch_minimal_flex_job",
            project_id=PROJECT_ID,
            location=REGION,
            body={
                "launchParameter": {
                    # Job name must be unique per project+region
                    "jobName": "sales-etl-minimal-{{ ds_nodash }}-{{ macros.uuid7() }}",
                    # GCS path to the Flex Template spec (JSON file built by gcloud dataflow flex-template build)
                    "containerSpecGcsPath": FLEX_TEMPLATE_PATH,
                    # Template-specific parameters — defined in the template's metadata.json
                    "parameters": {
                        "input_gcs_path":  f"gs://{GCS_BUCKET}/landing/sales/{{{{ ds }}}}/*.json",
                        "output_bq_table": f"{PROJECT_ID}:{DATASET}.sales_dataflow_output",
                    },
                }
            },
            do_xcom_push=True,       # pushes job_id to XCom key 'dataflow_job_id'
            doc_md=(
                "Minimal Flex Template launch — only jobName, containerSpecGcsPath, "
                "and required template parameters."
            ),
        )

        # Sensor waits for the minimal job to reach terminal state
        wait_minimal_job = DataflowJobStatusSensor(
            task_id="wait_minimal_job",
            project_id=PROJECT_ID,
            location=REGION,
            job_id="{{ ti.xcom_pull(task_ids='minimal_flex_launch.launch_minimal_flex_job', key='dataflow_job_id') }}",
            expected_statuses={"JOB_STATE_DONE"},
            timeout=60 * 60 * 2,     # 2-hour timeout
            poke_interval=30,
            mode="reschedule",
            doc_md="Sensor: poll minimal Flex Template job every 30s until JOB_STATE_DONE.",
        )

        launch_minimal_flex_job >> wait_minimal_job

    # -----------------------------------------------------------------------
    # Group B: Production Flex Template launch — full configuration
    # -----------------------------------------------------------------------
    with TaskGroup(
        group_id="production_flex_launch",
        tooltip="Full production-grade DataflowStartFlexTemplateOperator configuration"
    ) as production_group:

        launch_production_flex_job = DataflowStartFlexTemplateOperator(
            task_id="launch_production_flex_job",
            project_id=PROJECT_ID,
            location=REGION,
            body={
                "launchParameter": {
                    "jobName": f"sales-etl-prod-{{{{ ds_nodash }}}}",
                    "containerSpecGcsPath": FLEX_TEMPLATE_PATH,

                    # ---- Template parameters ----
                    "parameters": {
                        "input_gcs_path":   f"gs://{GCS_BUCKET}/landing/sales/{{{{ ds }}}}/*.json",
                        "output_bq_table":  f"{PROJECT_ID}:{DATASET}.sales_dataflow_output",
                        "temp_location":    f"gs://{GCS_BUCKET}/dataflow/temp",
                        "staging_location": f"gs://{GCS_BUCKET}/dataflow/staging",
                        # Custom template parameters (defined by the Flex Template author)
                        "batch_size":       "1000",
                        "error_threshold":  "0.01",       # fail if >1% of records error
                        "partition_date":   "{{ ds }}",
                    },

                    # ---- Dataflow runner environment ----
                    "environment": {
                        # Worker configuration
                        "numWorkers":        1,
                        "maxWorkers":        10,           # autoscaling ceiling
                        "machineType":       "n1-standard-4",
                        "diskSizeGb":        100,

                        # Storage locations for job artifacts
                        "tempLocation":    f"gs://{GCS_BUCKET}/dataflow/temp",
                        "stagingLocation": f"gs://{GCS_BUCKET}/dataflow/staging",

                        # Networking — use private IPs where possible
                        "network":    "default",
                        "subnetwork": f"regions/{REGION}/subnetworks/default",
                        "usePublicIps": False,

                        # Service account for Dataflow workers (must have BQ/GCS permissions)
                        "serviceAccountEmail": (
                            f"your-dataflow-sa@{PROJECT_ID}.iam.gserviceaccount.com"
                        ),

                        # Labels for cost attribution and filtering in Cloud Billing
                        "additionalUserLabels": {
                            "team":        "data-engineering",
                            "environment": "production",
                            "dag":         "dataflow_flex_template_dag",
                        },

                        # Dataflow experiments / features
                        "additionalExperiments": [
                            "use_runner_v2",
                            "enable_recommendations",
                        ],

                        # Streaming Engine (set True for streaming pipelines)
                        "enableStreamingEngine": False,

                        # KMS key for data encryption (optional)
                        # "kmsKeyName": f"projects/{PROJECT_ID}/locations/{REGION}/keyRings/...",
                    },
                }
            },
            do_xcom_push=True,
            doc_md=(
                "Full production Flex Template launch with: "
                "autoscaling (1–10 workers), private IPs, custom SA, "
                "cost labels, and Dataflow Runner v2."
            ),
        )

        # ---- AutoScaling events sensor (observational — does not block) ----
        monitor_autoscaling = DataflowJobAutoScalingEventsSensor(
            task_id="monitor_autoscaling_events",
            project_id=PROJECT_ID,
            location=REGION,
            job_id="{{ ti.xcom_pull(task_ids='production_flex_launch.launch_production_flex_job', key='dataflow_job_id') }}",
            # Sensor succeeds when any autoscaling event is received
            # For a blocking wait, set fail_on_terminal_state=True
            timeout=300,
            poke_interval=30,
            mode="reschedule",
            doc_md=(
                "Observe autoscaling events during the Dataflow job run. "
                "Non-blocking — proceeds to messages sensor after 5 min."
            ),
        )

        # ---- Job messages sensor (streams logs into Airflow logs) ----
        monitor_job_messages = DataflowJobMessagesSensor(
            task_id="monitor_job_messages",
            project_id=PROJECT_ID,
            location=REGION,
            job_id="{{ ti.xcom_pull(task_ids='production_flex_launch.launch_production_flex_job', key='dataflow_job_id') }}",
            # Filter by minimum importance: JOB_MESSAGE_WARNING or JOB_MESSAGE_ERROR
            fail_on_terminal_state=False,
            timeout=300,
            poke_interval=30,
            mode="reschedule",
            doc_md=(
                "Stream Dataflow job messages (warnings/errors) into Airflow logs. "
                "Non-blocking — allows parallel status sensor to drive completion."
            ),
        )

        # ---- Status sensor — authoritative completion check ----
        wait_production_job = DataflowJobStatusSensor(
            task_id="wait_production_job",
            project_id=PROJECT_ID,
            location=REGION,
            job_id="{{ ti.xcom_pull(task_ids='production_flex_launch.launch_production_flex_job', key='dataflow_job_id') }}",
            expected_statuses={"JOB_STATE_DONE"},
            timeout=60 * 60 * 4,     # 4 hours
            poke_interval=60,
            mode="reschedule",
            doc_md=(
                "Block until the production Dataflow job reaches JOB_STATE_DONE "
                "(or fails with JOB_STATE_FAILED / JOB_STATE_CANCELLED)."
            ),
        )

        # Launch → parallel monitoring + status polling → done
        launch_production_flex_job >> [monitor_autoscaling, monitor_job_messages, wait_production_job]

    # -----------------------------------------------------------------------
    # Classic Template reference task (for migration reference)
    # -----------------------------------------------------------------------
    classic_template_reference = DataflowTemplatedJobStartOperator(
        task_id="classic_template_reference",
        project_id=PROJECT_ID,
        location=REGION,
        template=CLASSIC_TEMPLATE_PATH,
        job_name="classic-template-ref-{{ ds_nodash }}",
        parameters={
            "javascriptTextTransformGcsPath":        "",
            "JSONPath":                              "",
            "javascriptTextTransformFunctionName":   "",
            "outputTable":   f"{PROJECT_ID}:{DATASET}.classic_template_output",
            "inputFilePattern": f"gs://{GCS_BUCKET}/landing/text/*.txt",
            "bigQueryLoadingTemporaryDirectory": f"gs://{GCS_BUCKET}/dataflow/temp",
        },
        dataflow_default_options={
            "project":         PROJECT_ID,
            "region":          REGION,
            "tempLocation":    f"gs://{GCS_BUCKET}/dataflow/temp",
            "stagingLocation": f"gs://{GCS_BUCKET}/dataflow/staging",
        },
        do_xcom_push=True,
        doc_md=(
            "Reference: DataflowTemplatedJobStartOperator for Classic Templates "
            "(pre-Flex Template API). Most teams should use Flex Templates instead."
        ),
    )

    # -----------------------------------------------------------------------
    # Log all job IDs
    # -----------------------------------------------------------------------
    log_jobs = PythonOperator(
        task_id="log_job_ids",
        python_callable=log_job_ids,
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
        doc_md="Pull all Dataflow job IDs from XCom and log Cloud Console links.",
    )

    # -----------------------------------------------------------------------
    # Wire dependencies
    # -----------------------------------------------------------------------
    start >> [minimal_group, production_group, classic_template_reference]
    [minimal_group, production_group, classic_template_reference] >> log_jobs >> end
