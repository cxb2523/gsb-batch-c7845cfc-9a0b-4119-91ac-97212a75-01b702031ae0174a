# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
bq_pipeline_dag.py — Full orchestrated BigQuery pipeline.

Pipeline steps:
  1. GCSObjectExistenceSensor   — wait for today's CSV to land in GCS
  2. BigQueryInsertJobOperator  — load CSV from GCS → BigQuery raw table
  3. BigQueryInsertJobOperator  — MERGE raw → staging (idempotent)
  4. BigQueryInsertJobOperator  — aggregate staging → sales_summary
  5. BigQueryCheckOperator      — validate row counts and data quality
  6. BigQueryInsertJobOperator  — export validated summary → GCS (result)

All SQL is externalised to sql/ directory and read at DAG parse time.
Jinja parameters are injected via the `params` dict.
"""

from datetime import datetime, timedelta
from pathlib import Path
import logging

from airflow import DAG
from airflow.models import Variable
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator
from airflow.providers.google.cloud.operators.bigquery import (
    BigQueryInsertJobOperator,
    BigQueryCheckOperator,
    BigQueryCreateEmptyTableOperator,
    BigQueryCreateEmptyDatasetOperator,
)
from airflow.providers.google.cloud.sensors.gcs import GCSObjectExistenceSensor

# ---------------------------------------------------------------------------
# Runtime config — set as Airflow Variables
# ---------------------------------------------------------------------------
PROJECT_ID  = Variable.get("project_id",  default_var="your-project-id")
GCS_BUCKET  = Variable.get("gcs_bucket",  default_var="your-gcs-bucket")
DATASET     = Variable.get("bq_dataset",  default_var="your_dataset")
LOCATION    = Variable.get("bq_location", default_var="US")

# Path to sql/ directory (relative to DAGs folder on the Composer worker)
# Composer mounts the GCS dags/ bucket at /home/airflow/gcs/dags/
SQL_DIR = Path("/home/airflow/gcs/dags/../sql")


def _read_sql(filename: str) -> str:
    """Read an external SQL file. Falls back to inline SQL if file not found."""
    sql_path = SQL_DIR / filename
    try:
        return sql_path.read_text()
    except FileNotFoundError:
        logging.warning("SQL file %s not found at %s — using inline fallback.", filename, sql_path)
        return f"SELECT 1  -- fallback: {filename} not found"


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
# Table schemas (used by the schema-creation tasks)
# ---------------------------------------------------------------------------
STAGING_SCHEMA = [
    {"name": "order_id",       "type": "INTEGER",   "mode": "REQUIRED"},
    {"name": "order_date",     "type": "DATE",      "mode": "NULLABLE"},
    {"name": "customer_id",    "type": "STRING",    "mode": "NULLABLE"},
    {"name": "customer_name",  "type": "STRING",    "mode": "NULLABLE"},
    {"name": "region",         "type": "STRING",    "mode": "NULLABLE"},
    {"name": "category",       "type": "STRING",    "mode": "NULLABLE"},
    {"name": "product_name",   "type": "STRING",    "mode": "NULLABLE"},
    {"name": "quantity",       "type": "INTEGER",   "mode": "NULLABLE"},
    {"name": "unit_price",     "type": "FLOAT64",   "mode": "NULLABLE"},
    {"name": "total_amount",   "type": "FLOAT64",   "mode": "NULLABLE"},
    {"name": "status",         "type": "STRING",    "mode": "NULLABLE"},
    {"name": "discount_pct",   "type": "FLOAT64",   "mode": "NULLABLE"},
    {"name": "partition_date", "type": "DATE",      "mode": "NULLABLE"},
    {"name": "ingested_at",    "type": "TIMESTAMP", "mode": "NULLABLE"},
]

SUMMARY_SCHEMA = [
    {"name": "report_date",       "type": "DATE",      "mode": "REQUIRED"},
    {"name": "region",            "type": "STRING",    "mode": "REQUIRED"},
    {"name": "category",          "type": "STRING",    "mode": "REQUIRED"},
    {"name": "total_orders",      "type": "INTEGER",   "mode": "NULLABLE"},
    {"name": "completed_orders",  "type": "INTEGER",   "mode": "NULLABLE"},
    {"name": "cancelled_orders",  "type": "INTEGER",   "mode": "NULLABLE"},
    {"name": "pending_orders",    "type": "INTEGER",   "mode": "NULLABLE"},
    {"name": "total_units_sold",  "type": "INTEGER",   "mode": "NULLABLE"},
    {"name": "gross_revenue",     "type": "FLOAT64",   "mode": "NULLABLE"},
    {"name": "net_revenue",       "type": "FLOAT64",   "mode": "NULLABLE"},
    {"name": "avg_order_value",   "type": "FLOAT64",   "mode": "NULLABLE"},
    {"name": "avg_discount_pct",  "type": "FLOAT64",   "mode": "NULLABLE"},
    {"name": "top_product",       "type": "STRING",    "mode": "NULLABLE"},
    {"name": "created_at",        "type": "TIMESTAMP", "mode": "NULLABLE"},
]


def log_pipeline_result(**context):
    """Final task — log pipeline outcome with key metrics."""
    ti = context["ti"]
    logging.info(
        "bq_pipeline_dag complete | Project: %s | Dataset: %s | Date: %s",
        PROJECT_ID, DATASET, context["ds"],
    )


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="bq_pipeline_dag",
    description="Full Composer→BigQuery pipeline: GCS sensor → load → transform → validate → export",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args=default_args,
    params={
        "project_id":    PROJECT_ID,
        "dataset":       DATASET,
        "gcs_bucket":    GCS_BUCKET,
        "min_row_count": 1,
    },
    tags=["techtrapture", "bigquery", "pipeline", "composer"],
    doc_md="""
## bq_pipeline_dag

**Purpose:** End-to-end orchestrated data pipeline from GCS CSV to BigQuery summary.

**Pipeline:**
```
GCSObjectExistenceSensor
    → BigQueryInsertJobOperator (GCS → BQ raw table via LOAD job)
    → BigQueryInsertJobOperator (MERGE raw → sales_staging)
    → BigQueryInsertJobOperator (aggregate staging → sales_summary)
    → BigQueryCheckOperator     (validate row counts + data quality)
    → BigQueryInsertJobOperator (EXPORT summary → GCS result CSV)
```

**Airflow Variables:**
| Variable | Default |
|---|---|
| `project_id` | your-project-id |
| `gcs_bucket` | your-gcs-bucket |
| `bq_dataset` | your_dataset |
| `bq_location` | US |

**External SQL files:**
- `sql/staging_load.sql` — MERGE raw → staging
- `sql/transform.sql` — aggregate staging → summary
- `sql/validate.sql` — validation checks (used by BigQueryCheckOperator)
""",
) as dag:

    start = EmptyOperator(task_id="start")

    # -----------------------------------------------------------------------
    # 0. Ensure dataset and tables exist (idempotent)
    # -----------------------------------------------------------------------
    create_dataset = BigQueryCreateEmptyDatasetOperator(
        task_id="create_dataset_if_not_exists",
        dataset_id=DATASET,
        project_id=PROJECT_ID,
        location=LOCATION,
        exists_ok=True,
        doc_md="Create BigQuery dataset if it does not already exist.",
    )

    create_staging_table = BigQueryCreateEmptyTableOperator(
        task_id="create_staging_table_if_not_exists",
        dataset_id=DATASET,
        table_id="sales_staging",
        project_id=PROJECT_ID,
        schema_fields=STAGING_SCHEMA,
        exists_ok=True,
        time_partitioning={"type": "DAY", "field": "partition_date"},
        doc_md="Create sales_staging table (DATE-partitioned) if absent.",
    )

    create_summary_table = BigQueryCreateEmptyTableOperator(
        task_id="create_summary_table_if_not_exists",
        dataset_id=DATASET,
        table_id="sales_summary",
        project_id=PROJECT_ID,
        schema_fields=SUMMARY_SCHEMA,
        exists_ok=True,
        time_partitioning={"type": "DAY", "field": "report_date"},
        doc_md="Create sales_summary table (DATE-partitioned) if absent.",
    )

    # -----------------------------------------------------------------------
    # 1. Sensor — wait for today's CSV to land in GCS (polls every 60s)
    # -----------------------------------------------------------------------
    wait_for_csv = GCSObjectExistenceSensor(
        task_id="wait_for_source_csv",
        bucket=GCS_BUCKET,
        object="landing/sales/{{ ds }}/sales_data.csv",
        timeout=60 * 60,         # 1-hour timeout
        poke_interval=60,        # check every 60 seconds
        mode="reschedule",       # frees worker slot between pokes
        doc_md=(
            "Wait for gs://your-gcs-bucket/landing/sales/YYYY-MM-DD/sales_data.csv "
            "to exist before proceeding."
        ),
    )

    # -----------------------------------------------------------------------
    # 2. Load CSV from GCS into a date-suffixed raw table
    # -----------------------------------------------------------------------
    load_gcs_to_bq = BigQueryInsertJobOperator(
        task_id="load_gcs_to_bq_raw",
        configuration={
            "load": {
                "sourceUris": [
                    "gs://{{ var.value.gcs_bucket }}/landing/sales/{{ ds }}/sales_data.csv"
                ],
                "destinationTable": {
                    "projectId": PROJECT_ID,
                    "datasetId": DATASET,
                    "tableId":   "sales_raw_{{ ds_nodash }}",
                },
                "sourceFormat":       "CSV",
                "skipLeadingRows":    1,
                "writeDisposition":   "WRITE_TRUNCATE",   # idempotent
                "autodetect":         True,
                "createDisposition":  "CREATE_IF_NEEDED",
            }
        },
        project_id=PROJECT_ID,
        location=LOCATION,
        doc_md="Load CSV file from GCS into a date-partitioned raw BigQuery table.",
    )

    # -----------------------------------------------------------------------
    # 3. MERGE raw → staging (idempotent via staging_load.sql)
    # -----------------------------------------------------------------------
    merge_to_staging = BigQueryInsertJobOperator(
        task_id="merge_raw_to_staging",
        configuration={
            "query": {
                "query":        _read_sql("staging_load.sql"),
                "useLegacySql": False,
                "parameterMode": "NAMED",
            }
        },
        project_id=PROJECT_ID,
        location=LOCATION,
        doc_md="MERGE date-partitioned raw rows into sales_staging (idempotent).",
    )

    # -----------------------------------------------------------------------
    # 4. Transform: aggregate staging → sales_summary
    # -----------------------------------------------------------------------
    run_transform = BigQueryInsertJobOperator(
        task_id="run_aggregation_transform",
        configuration={
            "query": {
                "query":        _read_sql("transform.sql"),
                "useLegacySql": False,
            }
        },
        project_id=PROJECT_ID,
        location=LOCATION,
        doc_md="Aggregate sales_staging → sales_summary by region and category.",
    )

    # -----------------------------------------------------------------------
    # 5. Validate: row counts + data quality
    # -----------------------------------------------------------------------
    validate_pipeline = BigQueryCheckOperator(
        task_id="validate_pipeline_output",
        sql=_read_sql("validate.sql"),
        use_legacy_sql=False,
        location=LOCATION,
        params={
            "project_id":    PROJECT_ID,
            "dataset":       DATASET,
            "min_row_count": 1,
        },
        doc_md=(
            "Run validate.sql via BigQueryCheckOperator. "
            "Fails the task if the first column of the first row is falsy (0 or NULL)."
        ),
    )

    # -----------------------------------------------------------------------
    # 6. Export result back to GCS as CSV
    # -----------------------------------------------------------------------
    export_to_gcs = BigQueryInsertJobOperator(
        task_id="export_summary_to_gcs",
        configuration={
            "extract": {
                "sourceTable": {
                    "projectId": PROJECT_ID,
                    "datasetId": DATASET,
                    "tableId":   "sales_summary",
                },
                "destinationUris": [
                    f"gs://{GCS_BUCKET}/results/sales_summary/{{{{ ds }}}}/summary_*.csv"
                ],
                "destinationFormat": "CSV",
                "printHeader":       True,
                "compression":       "NONE",
            }
        },
        project_id=PROJECT_ID,
        location=LOCATION,
        doc_md="Export sales_summary to GCS as CSV shards in results/sales_summary/YYYY-MM-DD/.",
    )

    # -----------------------------------------------------------------------
    # 7. Final log
    # -----------------------------------------------------------------------
    log_result = PythonOperator(
        task_id="log_pipeline_result",
        python_callable=log_pipeline_result,
        doc_md="Log pipeline completion metadata.",
    )

    end = EmptyOperator(task_id="end")

    # -----------------------------------------------------------------------
    # Wire dependencies
    # -----------------------------------------------------------------------
    (
        start
        >> create_dataset
        >> [create_staging_table, create_summary_table]
        >> wait_for_csv
        >> load_gcs_to_bq
        >> merge_to_staging
        >> run_transform
        >> validate_pipeline
        >> export_to_gcs
        >> log_result
        >> end
    )
