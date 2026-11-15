# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
branching_dag.py — BranchPythonOperator routing based on day of week.

Demonstrates:
  - BranchPythonOperator for conditional task routing
  - Returning a task_id (or list) from the branch callable
  - Downstream tasks that are skipped vs executed
  - EmptyOperator as join/merge point after branches
  - How Airflow handles trigger_rule="none_failed_min_one_success"
    to converge multiple branches back to a single downstream task
"""

from datetime import datetime, timedelta
import logging

from airflow import DAG
from airflow.operators.python import PythonOperator, BranchPythonOperator
from airflow.operators.empty import EmptyOperator
from airflow.operators.bash import BashOperator
from airflow.models import Variable
from airflow.utils.trigger_rule import TriggerRule

# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------
PROJECT_ID = Variable.get("project_id", default_var="your-project-id")
GCS_BUCKET = Variable.get("gcs_bucket", default_var="your-gcs-bucket")

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
# Branch callable — routes to different tasks by day of week
# ---------------------------------------------------------------------------

def choose_pipeline_by_day(**context):
    """
    Branch based on the logical date's day of week.

    weekday() returns:
      0 = Monday, 1 = Tuesday, ..., 4 = Friday, 5 = Saturday, 6 = Sunday

    Routes:
      Mon/Tue/Wed/Thu → run_incremental_load  (weekday business logic)
      Friday          → run_weekly_aggregation (end-of-week rollup)
      Sat/Sun         → run_weekend_maintenance (maintenance window)
    """
    logical_date = context["logical_date"]          # pendulum datetime
    day_of_week = logical_date.weekday()
    day_name = logical_date.strftime("%A")

    logging.info("Logical date: %s | Day of week: %s (%d)", logical_date, day_name, day_of_week)

    if day_of_week in (0, 1, 2, 3):   # Mon–Thu
        logging.info("Routing to: run_incremental_load")
        return "run_incremental_load"
    elif day_of_week == 4:              # Friday
        logging.info("Routing to: run_weekly_aggregation")
        return "run_weekly_aggregation"
    else:                               # Saturday / Sunday
        logging.info("Routing to: run_weekend_maintenance")
        return "run_weekend_maintenance"


def choose_data_quality_check(**context):
    """
    Secondary branch — decides which quality check level to run
    based on an Airflow Variable (simulates feature flags / env config).
    """
    check_level = Variable.get("data_quality_level", default_var="standard")
    logging.info("Data quality check level: %s", check_level)

    if check_level == "strict":
        return "quality_check_strict"
    elif check_level == "minimal":
        return "quality_check_minimal"
    else:
        return "quality_check_standard"


# ---------------------------------------------------------------------------
# Python callables for branch tasks
# ---------------------------------------------------------------------------

def incremental_load(**context):
    ds = context["ds"]
    logging.info("Running incremental load for date: %s", ds)
    logging.info("Loading only new/changed records since last run.")
    context["ti"].xcom_push(key="load_type", value="incremental")
    context["ti"].xcom_push(key="records_processed", value=500)


def weekly_aggregation(**context):
    ds = context["ds"]
    logging.info("Running weekly aggregation for week ending: %s", ds)
    logging.info("Aggregating 7-day rolling window across all regions.")
    context["ti"].xcom_push(key="load_type", value="weekly_aggregation")
    context["ti"].xcom_push(key="records_processed", value=3500)


def weekend_maintenance(**context):
    ds = context["ds"]
    logging.info("Running weekend maintenance tasks for: %s", ds)
    logging.info("Vacuuming temp tables, refreshing statistics.")
    context["ti"].xcom_push(key="load_type", value="maintenance")
    context["ti"].xcom_push(key="records_processed", value=0)


def quality_check_strict(**context):
    logging.info("Running STRICT quality checks: null counts, referential integrity, schema validation.")


def quality_check_standard(**context):
    logging.info("Running STANDARD quality checks: row count, null check on key columns.")


def quality_check_minimal(**context):
    logging.info("Running MINIMAL quality checks: row count only.")


def post_run_summary(**context):
    """Converged task — runs regardless of which branch was taken."""
    ti = context["ti"]
    # XCom pulls across all possible upstream task_ids; returns None if skipped
    load_type = (
        ti.xcom_pull(task_ids="run_incremental_load", key="load_type")
        or ti.xcom_pull(task_ids="run_weekly_aggregation", key="load_type")
        or ti.xcom_pull(task_ids="run_weekend_maintenance", key="load_type")
        or "unknown"
    )
    records = (
        ti.xcom_pull(task_ids="run_incremental_load", key="records_processed")
        or ti.xcom_pull(task_ids="run_weekly_aggregation", key="records_processed")
        or ti.xcom_pull(task_ids="run_weekend_maintenance", key="records_processed")
        or 0
    )
    logging.info("Post-run summary | Load type: %s | Records: %s", load_type, records)


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="branching_dag",
    description="BranchPythonOperator routing pipeline tasks by day of week",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["techtrapture", "branching", "composer"],
    doc_md="""
## branching_dag

**Purpose:** Demonstrates Airflow's `BranchPythonOperator` for conditional task routing.

The DAG runs daily and routes to different processing tasks depending on the
day of the week:

| Day | Branch taken |
|---|---|
| Mon – Thu | `run_incremental_load` |
| Friday | `run_weekly_aggregation` |
| Sat – Sun | `run_weekend_maintenance` |

After the primary branch, a second `BranchPythonOperator` selects the data
quality check level from an Airflow Variable (`data_quality_level`).
All branches converge at `post_run_summary` using
`trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS`.

**Airflow Variables:**
| Variable | Values | Default |
|---|---|---|
| `data_quality_level` | strict / standard / minimal | standard |
| `project_id` | GCP project | your-project-id |
""",
) as dag:

    # --- Pipeline start sentinel ----------------------------------------
    start = EmptyOperator(task_id="start")

    # --- Primary branch: day-of-week routing ----------------------------
    branch_by_day = BranchPythonOperator(
        task_id="branch_by_day",
        python_callable=choose_pipeline_by_day,
        doc_md="Route to incremental/weekly/maintenance based on day of week.",
    )

    # --- Branch A: weekday incremental load -----------------------------
    run_incremental_load = PythonOperator(
        task_id="run_incremental_load",
        python_callable=incremental_load,
        doc_md="Load only records changed since the previous DAG run.",
    )

    # --- Branch B: Friday weekly aggregation ----------------------------
    run_weekly_aggregation = PythonOperator(
        task_id="run_weekly_aggregation",
        python_callable=weekly_aggregation,
        doc_md="Aggregate the full week's data into summary tables.",
    )

    # --- Branch C: weekend maintenance ----------------------------------
    run_weekend_maintenance = PythonOperator(
        task_id="run_weekend_maintenance",
        python_callable=weekend_maintenance,
        doc_md="Vacuum temp tables and refresh statistics over the weekend.",
    )

    # --- Join after primary branch (none_failed_min_one_success) --------
    # This task runs as long as at least one upstream branch succeeded.
    # Skipped branches do not count as failures.
    after_primary_branch = EmptyOperator(
        task_id="after_primary_branch",
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
        doc_md="Join/merge point after the primary branch.",
    )

    # --- Secondary branch: quality check level --------------------------
    branch_quality_level = BranchPythonOperator(
        task_id="branch_quality_level",
        python_callable=choose_data_quality_check,
        doc_md="Route to the appropriate quality check based on Airflow Variable.",
    )

    quality_check_strict = PythonOperator(
        task_id="quality_check_strict",
        python_callable=quality_check_strict,
        doc_md="Strict: null counts, referential integrity, schema validation.",
    )

    quality_check_standard = PythonOperator(
        task_id="quality_check_standard",
        python_callable=quality_check_standard,
        doc_md="Standard: row count + null check on key columns.",
    )

    quality_check_minimal = PythonOperator(
        task_id="quality_check_minimal",
        python_callable=quality_check_minimal,
        doc_md="Minimal: row count only.",
    )

    # --- Converge after quality checks ----------------------------------
    after_quality_check = EmptyOperator(
        task_id="after_quality_check",
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
        doc_md="Join/merge point after quality check branch.",
    )

    # --- Bash: log result to Cloud Logging ------------------------------
    log_to_cloud_logging = BashOperator(
        task_id="log_to_cloud_logging",
        bash_command=(
            "echo '[branching_dag] Pipeline completed for {{ ds }}' && "
            "echo 'Day of week: {{ logical_date.strftime(\"%A\") }}'"
        ),
        doc_md="Emit a structured log line to Cloud Logging.",
    )

    # --- Final summary (converged) --------------------------------------
    post_run_summary_task = PythonOperator(
        task_id="post_run_summary",
        python_callable=post_run_summary,
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
        doc_md="Emit consolidated summary regardless of which branches ran.",
    )

    end = EmptyOperator(task_id="end")

    # --- Wire up dependencies -------------------------------------------
    start >> branch_by_day

    # Primary branch fans out to three tasks, then merges
    branch_by_day >> [run_incremental_load, run_weekly_aggregation, run_weekend_maintenance]
    [run_incremental_load, run_weekly_aggregation, run_weekend_maintenance] >> after_primary_branch

    # Secondary branch (quality checks)
    after_primary_branch >> branch_quality_level
    branch_quality_level >> [quality_check_strict, quality_check_standard, quality_check_minimal]
    [quality_check_strict, quality_check_standard, quality_check_minimal] >> after_quality_check

    # Convergence to summary and end
    after_quality_check >> log_to_cloud_logging >> post_run_summary_task >> end
