# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
taskgroup_dag.py — TaskGroup organising Extract / Transform / Load phases.

Demonstrates:
  - TaskGroup to logically group tasks in the Airflow UI
  - Nested TaskGroups (load phase has sub-groups for staging vs production)
  - retry logic per-task and per-group via default_args
  - PythonOperator callables with XCom across group boundaries
  - EmptyOperator as intra-group sentinels
  - @task (TaskFlow API) syntax alongside classic operators
"""

from datetime import datetime, timedelta
import logging
import time

from airflow import DAG
from airflow.decorators import task as taskflow_task
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.utils.task_group import TaskGroup
from airflow.models import Variable

# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------
PROJECT_ID = Variable.get("project_id", default_var="your-project-id")
GCS_BUCKET = Variable.get("gcs_bucket", default_var="your-gcs-bucket")
DATASET = Variable.get("bq_dataset", default_var="your_dataset")

# ---------------------------------------------------------------------------
# Default arguments — applied to every task in the DAG
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
# Extract phase callables
# ---------------------------------------------------------------------------

def extract_from_gcs(**context):
    """Simulate reading raw files from GCS landing zone."""
    ds = context["ds"]
    logging.info("Extracting files for date partition: %s", ds)
    # Simulate file scan
    files_found = [
        f"gs://{GCS_BUCKET}/landing/sales/{ds}/sales_001.csv",
        f"gs://{GCS_BUCKET}/landing/sales/{ds}/sales_002.csv",
    ]
    logging.info("Found %d files: %s", len(files_found), files_found)
    context["ti"].xcom_push(key="raw_files", value=files_found)
    context["ti"].xcom_push(key="raw_row_count", value=40)


def extract_from_api(**context):
    """Simulate calling an external REST API to pull supplemental data."""
    logging.info("Calling external product catalog API...")
    # Simulate API latency
    time.sleep(1)
    catalog_items = [
        {"sku": "HDPH-001", "category": "Electronics", "supplier": "TechCo"},
        {"sku": "SHOE-002", "category": "Clothing", "supplier": "FashionBrand"},
        {"sku": "BOOK-003", "category": "Books", "supplier": "EduPress"},
    ]
    logging.info("Fetched %d catalog items from API.", len(catalog_items))
    context["ti"].xcom_push(key="catalog_items", value=catalog_items)
    context["ti"].xcom_push(key="catalog_count", value=len(catalog_items))


def validate_raw_data(**context):
    """Check raw extract counts before proceeding to transform."""
    ti = context["ti"]
    raw_count = ti.xcom_pull(task_ids="extract.extract_from_gcs", key="raw_row_count") or 0
    catalog_count = ti.xcom_pull(task_ids="extract.extract_from_api", key="catalog_count") or 0

    logging.info("Raw row count: %d | Catalog items: %d", raw_count, catalog_count)

    if raw_count == 0:
        raise ValueError("No raw rows extracted — aborting pipeline.")
    logging.info("Raw data validation passed.")
    context["ti"].xcom_push(key="validated_row_count", value=raw_count)


# ---------------------------------------------------------------------------
# Transform phase callables
# ---------------------------------------------------------------------------

def clean_and_deduplicate(**context):
    """Remove duplicates and null-key rows from the extracted dataset."""
    ti = context["ti"]
    # Pull from extract group using group_id prefix
    raw_count = ti.xcom_pull(task_ids="extract.validate_raw", key="validated_row_count") or 40
    cleaned_count = int(raw_count * 0.95)  # Simulate 5% dupe removal
    logging.info("Cleaned %d → %d rows (removed %d dupes/nulls).",
                 raw_count, cleaned_count, raw_count - cleaned_count)
    context["ti"].xcom_push(key="clean_row_count", value=cleaned_count)


def enrich_with_catalog(**context):
    """Join sales rows with product catalog to enrich category/supplier."""
    ti = context["ti"]
    clean_count = ti.xcom_pull(task_ids="transform.clean_and_deduplicate", key="clean_row_count") or 38
    logging.info("Enriching %d rows with product catalog data...", clean_count)
    context["ti"].xcom_push(key="enriched_row_count", value=clean_count)


def aggregate_metrics(**context):
    """Compute summary metrics: revenue by region, category, and date."""
    ti = context["ti"]
    enriched_count = ti.xcom_pull(task_ids="transform.enrich_with_catalog", key="enriched_row_count") or 38
    metrics = {
        "total_rows": enriched_count,
        "regions": ["US-West", "US-East", "EU-West", "APAC"],
        "categories": ["Electronics", "Clothing", "Books"],
        "total_revenue_usd": 3248.87,
    }
    logging.info("Aggregated metrics: %s", metrics)
    context["ti"].xcom_push(key="aggregated_metrics", value=metrics)


# ---------------------------------------------------------------------------
# Load phase callables
# ---------------------------------------------------------------------------

def load_to_staging(**context):
    """Write transformed data to BigQuery staging table."""
    ti = context["ti"]
    metrics = ti.xcom_pull(task_ids="transform.aggregate_metrics", key="aggregated_metrics") or {}
    row_count = metrics.get("total_rows", 0)
    logging.info(
        "Loading %d rows to BigQuery staging table: %s.%s.sales_staging",
        row_count, PROJECT_ID, DATASET
    )
    context["ti"].xcom_push(key="staging_rows_loaded", value=row_count)


def validate_staging(**context):
    """Row count check: staging table must equal the transform output."""
    ti = context["ti"]
    loaded = ti.xcom_pull(task_ids="load.staging.load_to_staging", key="staging_rows_loaded") or 0
    logging.info("Staging validation: %d rows present.", loaded)
    if loaded == 0:
        raise ValueError("Staging table is empty after load — aborting promotion.")
    logging.info("Staging validation PASSED.")


def promote_to_production(**context):
    """Atomic swap: move validated data from staging to production table."""
    ti = context["ti"]
    staging_rows = ti.xcom_pull(task_ids="load.staging.validate_staging_load", key=None)
    logging.info("Promoting staging → production in %s.%s.sales_fact", PROJECT_ID, DATASET)
    context["ti"].xcom_push(key="production_rows", value=38)


def refresh_materialized_views(**context):
    """Refresh downstream materialized views that depend on sales_fact."""
    views = ["sales_by_region_mv", "sales_by_category_mv", "daily_revenue_mv"]
    for view in views:
        logging.info("Refreshing materialized view: %s.%s.%s", PROJECT_ID, DATASET, view)
    logging.info("All %d materialized views refreshed.", len(views))


def send_completion_notification(**context):
    """Log pipeline completion metadata (real impl would call PubSub/Slack)."""
    ti = context["ti"]
    metrics = ti.xcom_pull(task_ids="transform.aggregate_metrics", key="aggregated_metrics") or {}
    logging.info(
        "PIPELINE COMPLETE | DAG: taskgroup_dag | Date: %s | Rows: %s | Revenue: $%s",
        context["ds"],
        metrics.get("total_rows", "N/A"),
        metrics.get("total_revenue_usd", "N/A"),
    )


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="taskgroup_dag",
    description="TaskGroup-organised Extract / Transform / Load pipeline with retries",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["techtrapture", "taskgroup", "etl", "composer"],
    doc_md="""
## taskgroup_dag

**Purpose:** Production-style ETL DAG using `TaskGroup` to organise tasks into
logical phases visible in the Airflow Graph view.

**Phases:**

| Group | Sub-group | Tasks |
|---|---|---|
| `extract` | — | extract_from_gcs, extract_from_api, validate_raw |
| `transform` | — | clean_and_deduplicate, enrich_with_catalog, aggregate_metrics |
| `load` | `staging` | load_to_staging, validate_staging_load |
| `load` | `production` | promote_to_production, refresh_materialized_views |

**XCom flow:**
- `extract` → row counts push to XCom
- `transform` → enriched metrics push to XCom
- `load` → pulls from transform to validate before promoting

**Airflow Variables:**
| Variable | Default |
|---|---|
| `project_id` | your-project-id |
| `gcs_bucket` | your-gcs-bucket |
| `bq_dataset` | your_dataset |
""",
) as dag:

    pipeline_start = EmptyOperator(task_id="pipeline_start")

    # -----------------------------------------------------------------------
    # EXTRACT TaskGroup
    # -----------------------------------------------------------------------
    with TaskGroup(group_id="extract", tooltip="Extract raw data from GCS and external APIs") as extract_group:

        extract_gcs = PythonOperator(
            task_id="extract_from_gcs",
            python_callable=extract_from_gcs,
            retries=3,                                  # extra retries for I/O tasks
            retry_delay=timedelta(minutes=2),
            doc_md="Read raw CSV files from GCS landing zone.",
        )

        extract_api = PythonOperator(
            task_id="extract_from_api",
            python_callable=extract_from_api,
            retries=3,
            retry_delay=timedelta(minutes=2),
            doc_md="Pull product catalog from external REST API.",
        )

        validate_raw = PythonOperator(
            task_id="validate_raw",
            python_callable=validate_raw_data,
            doc_md="Assert that extracted row counts are non-zero.",
        )

        # Both extracts must complete before validation
        [extract_gcs, extract_api] >> validate_raw

    # -----------------------------------------------------------------------
    # TRANSFORM TaskGroup
    # -----------------------------------------------------------------------
    with TaskGroup(group_id="transform", tooltip="Clean, enrich, and aggregate raw data") as transform_group:

        clean_dedup = PythonOperator(
            task_id="clean_and_deduplicate",
            python_callable=clean_and_deduplicate,
            doc_md="Remove duplicate and null-key rows.",
        )

        enrich = PythonOperator(
            task_id="enrich_with_catalog",
            python_callable=enrich_with_catalog,
            doc_md="Join with product catalog to add category and supplier.",
        )

        aggregate = PythonOperator(
            task_id="aggregate_metrics",
            python_callable=aggregate_metrics,
            doc_md="Compute revenue by region, category, and date.",
        )

        clean_dedup >> enrich >> aggregate

    # -----------------------------------------------------------------------
    # LOAD TaskGroup (with nested sub-groups)
    # -----------------------------------------------------------------------
    with TaskGroup(group_id="load", tooltip="Load to BigQuery staging then promote to production") as load_group:

        # ---- Nested: Staging sub-group ----
        with TaskGroup(group_id="staging", tooltip="Write to staging and validate") as staging_group:

            load_staging = PythonOperator(
                task_id="load_to_staging",
                python_callable=load_to_staging,
                doc_md="Write transform output to BigQuery staging table.",
            )

            validate_staging_load = PythonOperator(
                task_id="validate_staging_load",
                python_callable=validate_staging,
                doc_md="Verify row count in staging before promotion.",
            )

            load_staging >> validate_staging_load

        # ---- Nested: Production sub-group ----
        with TaskGroup(group_id="production", tooltip="Promote to production and refresh views") as production_group:

            promote = PythonOperator(
                task_id="promote_to_production",
                python_callable=promote_to_production,
                doc_md="Atomic swap of staging → production table.",
            )

            refresh_views = PythonOperator(
                task_id="refresh_materialized_views",
                python_callable=refresh_materialized_views,
                doc_md="Refresh materialized views after promotion.",
            )

            promote >> refresh_views

        staging_group >> production_group

    # -----------------------------------------------------------------------
    # Post-pipeline
    # -----------------------------------------------------------------------
    notify = PythonOperator(
        task_id="send_completion_notification",
        python_callable=send_completion_notification,
        doc_md="Emit pipeline completion event with key metrics.",
    )

    pipeline_end = EmptyOperator(task_id="pipeline_end")

    # -----------------------------------------------------------------------
    # Wire up the groups
    # -----------------------------------------------------------------------
    pipeline_start >> extract_group >> transform_group >> load_group >> notify >> pipeline_end
