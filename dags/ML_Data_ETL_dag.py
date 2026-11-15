import json
import logging
import os
from datetime import datetime, timedelta

import pandas as pd
from airflow import DAG
from airflow.hooks.base import BaseHook
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from weather_pipeline import fetch_weather_data as fetch_weather_records
from weather_pipeline import insert_weather_data as upsert_weather_records

# Configure module-level logger
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Default DAG arguments
# ---------------------------------------------------------------------
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

# ---------------------------------------------------------------------
# DAG Definition
# ---------------------------------------------------------------------
with DAG(
    dag_id="weather_data_pipeline",
    default_args=default_args,
    description="Fetch weather data and insert into PostgreSQL",
    schedule="0 23 * * *",
    start_date=datetime(2025, 1, 4),
    catchup=False,
) as dag:
    # -----------------------------------------------------------------
    # Step 1: Load Locations
    # -----------------------------------------------------------------
    def load_locations():
        """
        Load city locations from a CSV file and return as a JSON string.
        """
        file_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data",
            "locations.csv",
        )
        locations = pd.read_csv(file_path).to_dict(orient="records")
        return json.dumps(locations)

    load_locations_task = PythonOperator(
        task_id="load_locations",
        python_callable=load_locations,
    )

    # -----------------------------------------------------------------
    # Step 2: Fetch Weather Data
    # -----------------------------------------------------------------
    def fetch_weather_data(locations):
        """
        Fetch the current weather data for each location from the
        OpenWeather API.

        Per-city failures (request error, timeout or a body missing
        main/wind/weather) are collected instead of being swallowed: the
        returned XCom payload contains both the successful records and a
        ``failures`` list. The task raises only when every city fails.
        """
        connection = BaseHook.get_connection("openweather_api_key")
        payload = fetch_weather_records(
            locations=json.loads(locations),
            connection=connection,
        )
        logger.info(
            "Fetched %s weather records; %s city/cities failed: %s",
            len(payload["weather_data"]),
            len(payload["failures"]),
            [item["city"] for item in payload["failures"]],
        )
        return payload

    fetch_weather_data_task = PythonOperator(
        task_id="fetch_weather_data",
        python_callable=fetch_weather_data,
        op_args=["{{ ti.xcom_pull(task_ids='load_locations') }}"],
    )

    # -----------------------------------------------------------------
    # Step 3: Insert Weather Data into PostgreSQL
    # -----------------------------------------------------------------
    def insert_weather_data(weather_payload):
        """
        Upsert the fetched weather data into the PostgreSQL ``weather`` table.

        Rows are keyed by (city, date), so re-running the same batch updates
        the existing rows instead of creating duplicates. The fetched failure
        list is surfaced in the logs and forwarded through XCom.
        """
        postgres_hook = PostgresHook(postgres_conn_id="postgres_weather_db")
        summary = upsert_weather_records(weather_payload, postgres_hook)

        logger.info("Successful inserts/updates: %s", summary["inserted"])
        if summary["failures"]:
            logger.warning(
                "Failed cities (no row written): %s",
                [item["city"] for item in summary["failures"]],
            )
        return summary

    insert_weather_data_task = PythonOperator(
        task_id="insert_weather_data",
        python_callable=insert_weather_data,
        op_args=["{{ ti.xcom_pull(task_ids='fetch_weather_data') }}"],
    )

    # -----------------------------------------------------------------
    # Task Dependencies
    # -----------------------------------------------------------------
    load_locations_task >> fetch_weather_data_task >> insert_weather_data_task
