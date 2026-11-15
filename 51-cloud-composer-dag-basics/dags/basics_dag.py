# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
basics_dag.py — Cloud Composer DAG Basics (Hello World equivalent)

Demonstrates:
  - BashOperator
  - PythonOperator
  - EmptyOperator (start/end sentinels)
  - XCom push/pull between tasks
  - Default args with retry logic
  - Jinja templating (ds, ts macros)
"""

from datetime import datetime, timedelta
import csv
import io
import logging

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from airflow.operators.empty import EmptyOperator
from airflow.models import Variable

# ---------------------------------------------------------------------------
# Runtime config — set these Airflow Variables before triggering:
#   gcs_bucket       → your-gcs-bucket
#   project_id       → your-project-id
# ---------------------------------------------------------------------------
GCS_BUCKET = Variable.get("gcs_bucket", default_var="your-gcs-bucket")
PROJECT_ID = Variable.get("project_id", default_var="your-project-id")

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

def print_run_context(**context):
    """Log key runtime context info and push to XCom."""
    ds = context["ds"]                  # logical date string YYYY-MM-DD
    ts = context["ts"]                  # ISO-8601 timestamp
    run_id = context["run_id"]
    dag_id = context["dag"].dag_id

    log_msg = (
        f"DAG: {dag_id} | Run ID: {run_id} | "
        f"Logical date: {ds} | Timestamp: {ts}"
    )
    logging.info(log_msg)

    # XCom push — any task downstream can pull this
    context["ti"].xcom_push(key="run_summary", value={
        "dag_id": dag_id,
        "logical_date": ds,
        "run_id": run_id,
    })
    return log_msg


def process_csv(**context):
    """
    Read the sample CSV (embedded inline for portability) and compute
    basic summary statistics. Pulls run_summary from XCom.
    """
    ti = context["ti"]
    run_summary = ti.xcom_pull(task_ids="log_run_context", key="run_summary")
    logging.info("Pulled XCom run_summary: %s", run_summary)

    # Inline CSV data (mirrors data/sample_input.csv)
    raw_csv = """\
order_id,order_date,customer_id,customer_name,region,category,product_name,quantity,unit_price,total_amount,status
1001,2024-01-05,C001,Alice Johnson,US-West,Electronics,Wireless Headphones,2,89.99,179.98,completed
1002,2024-01-06,C002,Bob Smith,US-East,Clothing,Running Shoes,1,129.99,129.99,completed
1003,2024-01-07,C003,Carol White,EU-West,Electronics,Laptop Stand,3,45.00,135.00,completed
1004,2024-01-08,C004,David Lee,APAC,Books,Data Engineering Book,2,39.99,79.98,completed
1005,2024-01-09,C005,Eva Martinez,US-West,Electronics,USB-C Hub,4,29.99,119.96,completed
1006,2024-01-10,C006,Frank Brown,US-East,Clothing,Winter Jacket,1,199.99,199.99,pending
1007,2024-01-11,C007,Grace Kim,EU-West,Books,Cloud Architecture Guide,3,49.99,149.97,completed
1008,2024-01-12,C008,Henry Wilson,APAC,Electronics,Smart Watch,1,299.99,299.99,completed
1009,2024-01-13,C009,Iris Chen,US-West,Clothing,Yoga Mat,2,59.99,119.98,completed
1010,2024-01-14,C010,Jack Taylor,US-East,Books,Python Programming,1,44.99,44.99,cancelled
1011,2024-01-15,C011,Karen Davis,EU-West,Electronics,Mechanical Keyboard,1,159.99,159.99,completed
1012,2024-01-16,C012,Liam Anderson,APAC,Clothing,Sneakers,2,89.99,179.98,completed
1013,2024-01-17,C013,Mia Thomas,US-West,Books,Machine Learning Guide,1,54.99,54.99,completed
1014,2024-01-18,C014,Noah Jackson,US-East,Electronics,Webcam HD,2,79.99,159.98,completed
1015,2024-01-19,C015,Olivia White,EU-West,Clothing,Sports T-Shirt,4,24.99,99.96,completed
1016,2024-01-20,C016,Peter Harris,APAC,Electronics,Portable SSD,1,119.99,119.99,pending
1017,2024-01-21,C017,Quinn Martin,US-West,Books,GCP Certification Guide,2,39.99,79.98,completed
1018,2024-01-22,C018,Rachel Garcia,US-East,Clothing,Denim Jeans,1,69.99,69.99,completed
1019,2024-01-23,C019,Sam Rodriguez,EU-West,Electronics,Bluetooth Speaker,3,49.99,149.97,completed
1020,2024-01-24,C020,Tina Nguyen,APAC,Books,Airflow in Practice,1,44.99,44.99,completed
"""
    reader = csv.DictReader(io.StringIO(raw_csv))
    rows = list(reader)

    total_orders = len(rows)
    total_revenue = sum(float(r["total_amount"]) for r in rows)
    completed = sum(1 for r in rows if r["status"] == "completed")
    by_category = {}
    for r in rows:
        by_category[r["category"]] = by_category.get(r["category"], 0) + float(r["total_amount"])

    summary = {
        "total_orders": total_orders,
        "total_revenue": round(total_revenue, 2),
        "completed_orders": completed,
        "revenue_by_category": {k: round(v, 2) for k, v in by_category.items()},
    }
    logging.info("CSV summary: %s", summary)

    # Push summary for downstream tasks
    ti.xcom_push(key="csv_summary", value=summary)
    return summary


def log_final_summary(**context):
    """Pull XCom from process_csv and log final pipeline summary."""
    ti = context["ti"]
    csv_summary = ti.xcom_pull(task_ids="process_csv_data", key="csv_summary")
    run_summary = ti.xcom_pull(task_ids="log_run_context", key="run_summary")

    logging.info("=" * 60)
    logging.info("PIPELINE COMPLETE")
    logging.info("Run context : %s", run_summary)
    logging.info("Data summary: %s", csv_summary)
    logging.info("=" * 60)


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="basics_dag",
    description="Cloud Composer DAG basics — BashOperator, PythonOperator, XCom",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["techtrapture", "basics", "composer"],
    doc_md="""
## basics_dag

**Purpose:** Hello-world equivalent for Cloud Composer / Airflow 2.x.

Demonstrates the most common Airflow primitives:
- `EmptyOperator` as pipeline start/end sentinels
- `BashOperator` for shell commands with Jinja date templating
- `PythonOperator` for arbitrary Python logic
- XCom push/pull for passing data between tasks
- Default args with automatic retry on failure

**Task flow:**
```
start → check_environment → log_run_context → process_csv_data → log_final_summary → end
```

**Airflow Variables used:**
| Variable | Default |
|---|---|
| `gcs_bucket` | your-gcs-bucket |
| `project_id` | your-project-id |
""",
) as dag:

    # --- Sentinels -------------------------------------------------------
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    # --- BashOperator: print environment info ----------------------------
    check_environment = BashOperator(
        task_id="check_environment",
        bash_command=(
            "echo '=== Cloud Composer Environment Check ===' && "
            "echo 'Python:' $(python --version) && "
            "echo 'Airflow:' $(airflow version) && "
            "echo 'Logical date: {{ ds }}' && "
            "echo 'Execution timestamp: {{ ts }}' && "
            "echo 'Project: {{ var.value.project_id }}' && "
            "echo 'GCS bucket: {{ var.value.gcs_bucket }}'"
        ),
        doc_md="Print runtime environment details using Jinja macros.",
    )

    # --- BashOperator: create a temp work directory ----------------------
    create_work_dir = BashOperator(
        task_id="create_work_dir",
        bash_command=(
            "WORK_DIR=/tmp/basics_dag/{{ ds_nodash }} && "
            "mkdir -p $WORK_DIR && "
            "echo \"Work dir created: $WORK_DIR\" && "
            "echo $WORK_DIR"
        ),
        doc_md="Create a temp working directory scoped to the logical date.",
    )

    # --- PythonOperator: log run context + push XCom --------------------
    log_run_context = PythonOperator(
        task_id="log_run_context",
        python_callable=print_run_context,
        doc_md="Log DAG runtime context and push run metadata to XCom.",
    )

    # --- PythonOperator: process CSV + push summary ---------------------
    process_csv_data = PythonOperator(
        task_id="process_csv_data",
        python_callable=process_csv,
        doc_md="Parse sample CSV, compute revenue stats, push XCom summary.",
    )

    # --- BashOperator: simulate writing output (uses XCom via Jinja) ----
    write_output = BashOperator(
        task_id="write_output",
        bash_command=(
            "echo '=== Writing pipeline output ===' && "
            "echo 'Logical date: {{ ds }}' && "
            "echo 'Total orders processed: {{ ti.xcom_pull(task_ids=\"process_csv_data\", key=\"csv_summary\")[\"total_orders\"] }}' && "
            "echo 'Total revenue: ${{ ti.xcom_pull(task_ids=\"process_csv_data\", key=\"csv_summary\")[\"total_revenue\"] }}'"
        ),
        doc_md="Echo pipeline results — shows XCom access inside Jinja.",
    )

    # --- PythonOperator: final summary ----------------------------------
    log_final_summary_task = PythonOperator(
        task_id="log_final_summary",
        python_callable=log_final_summary,
        doc_md="Pull all XCom values and print consolidated pipeline summary.",
    )

    # --- Task dependency chain ------------------------------------------
    (
        start
        >> check_environment
        >> create_work_dir
        >> log_run_context
        >> process_csv_data
        >> write_output
        >> log_final_summary_task
        >> end
    )
