# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
bq_operators_demo.py — Standalone reference demo of all major BigQuery Airflow operators.

Demonstrates:
  - BigQueryCreateEmptyDatasetOperator  — create dataset
  - BigQueryCreateEmptyTableOperator    — create table with schema
  - BigQueryInsertJobOperator           — INSERT / LOAD / EXPORT jobs
  - BigQueryCheckOperator               — single-value assertion
  - BigQueryValueCheckOperator          — compare a value against a threshold
  - BigQueryIntervalCheckOperator       — compare today's metric vs N days ago
  - BigQueryGetDataOperator             — pull rows directly into XCom
  - BigQueryDeleteTableOperator         — cleanup
  - BigQueryTableExistenceSensor        — wait for a table to appear
"""

from datetime import datetime, timedelta
import logging

from airflow import DAG
from airflow.models import Variable
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator
from airflow.providers.google.cloud.operators.bigquery import (
    BigQueryCreateEmptyDatasetOperator,
    BigQueryCreateEmptyTableOperator,
    BigQueryInsertJobOperator,
    BigQueryCheckOperator,
    BigQueryValueCheckOperator,
    BigQueryIntervalCheckOperator,
    BigQueryGetDataOperator,
    BigQueryDeleteTableOperator,
)
from airflow.providers.google.cloud.sensors.bigquery import BigQueryTableExistenceSensor

# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------
PROJECT_ID = Variable.get("project_id", default_var="your-project-id")
DATASET    = Variable.get("bq_dataset", default_var="your_dataset")
LOCATION   = Variable.get("bq_location", default_var="US")

# ---------------------------------------------------------------------------
# Default arguments
# ---------------------------------------------------------------------------
default_args = {
    "owner": "techtrapture",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
    "email_on_retry": False,
}

# ---------------------------------------------------------------------------
# Sample schema for the demo table
# ---------------------------------------------------------------------------
DEMO_SCHEMA = [
    {"name": "order_id",      "type": "INTEGER", "mode": "REQUIRED"},
    {"name": "order_date",    "type": "DATE",    "mode": "NULLABLE"},
    {"name": "region",        "type": "STRING",  "mode": "NULLABLE"},
    {"name": "category",      "type": "STRING",  "mode": "NULLABLE"},
    {"name": "total_amount",  "type": "FLOAT64", "mode": "NULLABLE"},
]


def log_fetched_rows(**context):
    """Pull rows fetched by BigQueryGetDataOperator from XCom and log them."""
    ti = context["ti"]
    rows = ti.xcom_pull(task_ids="get_sample_rows")
    logging.info("BigQueryGetDataOperator returned %d rows:", len(rows) if rows else 0)
    for i, row in enumerate(rows or []):
        logging.info("  Row %d: %s", i, row)


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="bq_operators_demo",
    description="Reference demo of all major BigQuery Airflow operators",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["techtrapture", "bigquery", "demo", "operators"],
    doc_md="""
## bq_operators_demo

**Purpose:** Standalone reference DAG showing every major BigQuery operator available
in `apache-airflow-providers-google`.

| Operator | Task ID | What it does |
|---|---|---|
| `BigQueryCreateEmptyDatasetOperator` | `create_demo_dataset` | Create dataset if absent |
| `BigQueryCreateEmptyTableOperator` | `create_demo_table` | Create table with schema |
| `BigQueryInsertJobOperator` (INSERT DML) | `insert_sample_rows` | Run INSERT SQL |
| `BigQueryTableExistenceSensor` | `wait_for_demo_table` | Sensor: block until table exists |
| `BigQueryCheckOperator` | `check_row_count` | Assert first result column is truthy |
| `BigQueryValueCheckOperator` | `check_revenue_value` | Assert a value equals expected |
| `BigQueryIntervalCheckOperator` | `check_interval_metric` | Compare today vs N-days-ago |
| `BigQueryGetDataOperator` | `get_sample_rows` | Pull rows into XCom |
| `BigQueryInsertJobOperator` (EXPORT) | `export_demo_table` | Export table to GCS |
| `BigQueryDeleteTableOperator` | `cleanup_demo_table` | Delete table (cleanup) |
""",
) as dag:

    start = EmptyOperator(task_id="start")
    end   = EmptyOperator(task_id="end")

    # -----------------------------------------------------------------------
    # 1. Create dataset
    # -----------------------------------------------------------------------
    create_demo_dataset = BigQueryCreateEmptyDatasetOperator(
        task_id="create_demo_dataset",
        dataset_id=DATASET,
        project_id=PROJECT_ID,
        location=LOCATION,
        exists_ok=True,
        doc_md="Create BigQuery dataset if not already present.",
    )

    # -----------------------------------------------------------------------
    # 2. Create demo table with explicit schema
    # -----------------------------------------------------------------------
    create_demo_table = BigQueryCreateEmptyTableOperator(
        task_id="create_demo_table",
        dataset_id=DATASET,
        table_id="orders_demo_{{ ds_nodash }}",
        project_id=PROJECT_ID,
        schema_fields=DEMO_SCHEMA,
        exists_ok=True,
        doc_md="Create orders_demo_YYYYMMDD table with an explicit schema.",
    )

    # -----------------------------------------------------------------------
    # 3. Insert sample rows using BigQueryInsertJobOperator (DML)
    # -----------------------------------------------------------------------
    insert_sample_rows = BigQueryInsertJobOperator(
        task_id="insert_sample_rows",
        configuration={
            "query": {
                "query": """
                    INSERT INTO `{{ params.project_id }}.{{ params.dataset }}.orders_demo_{{ ds_nodash }}`
                        (order_id, order_date, region, category, total_amount)
                    VALUES
                        (1, DATE('{{ ds }}'), 'US-West',  'Electronics', 179.98),
                        (2, DATE('{{ ds }}'), 'US-East',  'Clothing',    129.99),
                        (3, DATE('{{ ds }}'), 'EU-West',  'Books',       135.00),
                        (4, DATE('{{ ds }}'), 'APAC',     'Electronics', 299.99),
                        (5, DATE('{{ ds }}'), 'US-West',  'Books',        79.98)
                """,
                "useLegacySql": False,
            }
        },
        project_id=PROJECT_ID,
        location=LOCATION,
        params={"project_id": PROJECT_ID, "dataset": DATASET},
        doc_md="Insert 5 sample rows into the demo table using a DML INSERT.",
    )

    # -----------------------------------------------------------------------
    # 4. Sensor — wait for the table to exist (useful when table is created
    #    by an external process; here we created it in step 2 so it passes fast)
    # -----------------------------------------------------------------------
    wait_for_demo_table = BigQueryTableExistenceSensor(
        task_id="wait_for_demo_table",
        project_id=PROJECT_ID,
        dataset_id=DATASET,
        table_id="orders_demo_{{ ds_nodash }}",
        timeout=300,
        poke_interval=30,
        mode="reschedule",
        doc_md="Sensor: block until orders_demo_YYYYMMDD table exists in BigQuery.",
    )

    # -----------------------------------------------------------------------
    # 5. BigQueryCheckOperator — assert row count > 0
    #    Returns 1 if rows exist, 0 otherwise → 0 causes task failure
    # -----------------------------------------------------------------------
    check_row_count = BigQueryCheckOperator(
        task_id="check_row_count",
        sql="""
            SELECT COUNT(*) > 0
            FROM `{{ params.project_id }}.{{ params.dataset }}.orders_demo_{{ ds_nodash }}`
        """,
        use_legacy_sql=False,
        location=LOCATION,
        params={"project_id": PROJECT_ID, "dataset": DATASET},
        doc_md=(
            "BigQueryCheckOperator: fails if first result cell is 0 or NULL. "
            "Validates that the demo table has at least 1 row."
        ),
    )

    # -----------------------------------------------------------------------
    # 6. BigQueryValueCheckOperator — assert exact row count matches expected
    # -----------------------------------------------------------------------
    check_revenue_value = BigQueryValueCheckOperator(
        task_id="check_revenue_value",
        sql="""
            SELECT ROUND(SUM(total_amount), 2)
            FROM `{{ params.project_id }}.{{ params.dataset }}.orders_demo_{{ ds_nodash }}`
        """,
        pass_value=824.94,      # expected total from our 5 inserted rows
        tolerance=0.01,         # allow 1% tolerance
        use_legacy_sql=False,
        location=LOCATION,
        params={"project_id": PROJECT_ID, "dataset": DATASET},
        doc_md=(
            "BigQueryValueCheckOperator: asserts that total_amount sum equals "
            "824.94 (±1% tolerance)."
        ),
    )

    # -----------------------------------------------------------------------
    # 7. BigQueryIntervalCheckOperator — compare today's count vs 7 days ago
    #    Note: requires at least 7 days of historical data in the table.
    #    In a fresh setup this task will be skipped with a warning.
    # -----------------------------------------------------------------------
    check_interval_metric = BigQueryIntervalCheckOperator(
        task_id="check_interval_metric",
        table=f"{PROJECT_ID}.{DATASET}.orders_demo_{{{{ ds_nodash }}}}",
        metrics_thresholds={
            "COUNT(*)": 2.0,        # today's count must be within 2x of 7-days-ago
        },
        date_filter_column="order_date",
        days_back=-7,
        use_legacy_sql=False,
        location=LOCATION,
        doc_md=(
            "BigQueryIntervalCheckOperator: compare row count today vs 7 days ago. "
            "Fails if today's count is more than 2x the historical value."
        ),
    )

    # -----------------------------------------------------------------------
    # 8. BigQueryGetDataOperator — pull rows directly into XCom
    # -----------------------------------------------------------------------
    get_sample_rows = BigQueryGetDataOperator(
        task_id="get_sample_rows",
        dataset_id=DATASET,
        table_id="orders_demo_{{ ds_nodash }}",
        project_id=PROJECT_ID,
        max_results=5,
        selected_fields="order_id,region,category,total_amount",
        location=LOCATION,
        doc_md=(
            "BigQueryGetDataOperator: fetch up to 5 rows from the demo table "
            "and push them to XCom for downstream processing."
        ),
    )

    log_rows = PythonOperator(
        task_id="log_fetched_rows",
        python_callable=log_fetched_rows,
        doc_md="Pull and log the rows fetched by BigQueryGetDataOperator from XCom.",
    )

    # -----------------------------------------------------------------------
    # 9. Export table to GCS
    # -----------------------------------------------------------------------
    export_demo_table = BigQueryInsertJobOperator(
        task_id="export_demo_table",
        configuration={
            "extract": {
                "sourceTable": {
                    "projectId": PROJECT_ID,
                    "datasetId": DATASET,
                    "tableId":   "orders_demo_{{ ds_nodash }}",
                },
                "destinationUris": [
                    "gs://your-gcs-bucket/exports/orders_demo/{{ ds }}/orders_*.csv"
                ],
                "destinationFormat": "CSV",
                "printHeader":       True,
            }
        },
        project_id=PROJECT_ID,
        location=LOCATION,
        doc_md="Export orders_demo_YYYYMMDD to GCS as CSV (sharded).",
    )

    # -----------------------------------------------------------------------
    # 10. Cleanup — delete the demo table
    # -----------------------------------------------------------------------
    cleanup_demo_table = BigQueryDeleteTableOperator(
        task_id="cleanup_demo_table",
        deletion_dataset_table=f"{PROJECT_ID}.{DATASET}.orders_demo_{{{{ ds_nodash }}}}",
        ignore_if_missing=True,
        location=LOCATION,
        doc_md="Delete the temporary demo table (cleanup after export).",
    )

    # -----------------------------------------------------------------------
    # Wire dependencies
    # -----------------------------------------------------------------------
    (
        start
        >> create_demo_dataset
        >> create_demo_table
        >> insert_sample_rows
        >> wait_for_demo_table
        >> check_row_count
        >> check_revenue_value
        >> check_interval_metric
        >> get_sample_rows
        >> log_rows
        >> export_demo_table
        >> cleanup_demo_table
        >> end
    )
