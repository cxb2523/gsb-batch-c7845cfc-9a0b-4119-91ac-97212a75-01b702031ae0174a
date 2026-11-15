"""
BigQuery scheduled query creation via the Data Transfer Service API.

Creates a scheduled query that runs every 24 hours and writes a daily
aggregate table. The serverless alternative to Cloud Scheduler + Cloud Run
for recurring SQL — no infrastructure to manage.
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os
from datetime import datetime, timezone

from google.cloud import bigquery_datatransfer

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")

# The scheduled SQL writes (replaces) an aggregate table on every run.
SCHEDULED_SQL = f"""
    CREATE OR REPLACE TABLE `{PROJECT_ID}.{DATASET_ID}.daily_word_summary` AS
    SELECT
        corpus,
        COUNT(DISTINCT word) AS distinct_words,
        SUM(word_count)      AS total_words,
        CURRENT_TIMESTAMP()  AS refreshed_at
    FROM `bigquery-public-data.samples.shakespeare`
    GROUP BY corpus
"""


def create_scheduled_query(
    project_id: str = PROJECT_ID,
    dataset_id: str = DATASET_ID,
    schedule: str = "every 24 hours",
) -> str:
    """
    Create a BigQuery scheduled query via the Data Transfer Service API.

    Scheduled queries live in the DTS regional location matching the dataset
    (dataset in multi-region US -> location "us"). A timestamp suffix on the
    display name makes re-runs create distinguishable configs instead of piling
    up identical names.

    Args:
        project_id: GCP project to create the scheduled query in.
        dataset_id: Target dataset for the scheduled query output table.
        schedule:   DTS schedule expression (e.g. "every 24 hours").

    Returns:
        The full resource name of the created transfer config.
    """
    client = bigquery_datatransfer.DataTransferServiceClient()

    # Scheduled queries live in the DTS regional location matching the
    # dataset (your_dataset is multi-region US -> location "us").
    parent = f"projects/{project_id}/locations/us"

    # Unique-ish display name so re-runs create a distinguishable config
    # instead of silently piling up identical names.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    display_name = f"daily_word_summary_{stamp}"

    scheduled_sql = f"""
        CREATE OR REPLACE TABLE `{project_id}.{dataset_id}.daily_word_summary` AS
        SELECT
            corpus,
            COUNT(DISTINCT word) AS distinct_words,
            SUM(word_count)      AS total_words,
            CURRENT_TIMESTAMP()  AS refreshed_at
        FROM `bigquery-public-data.samples.shakespeare`
        GROUP BY corpus
    """

    transfer_config = bigquery_datatransfer.TransferConfig(
        display_name=display_name,
        data_source_id="scheduled_query",  # fixed id for scheduled queries
        params={
            "query": scheduled_sql,
            # No destination_table params needed — the DDL statement carries
            # its own destination (CREATE OR REPLACE TABLE).
        },
        schedule=schedule,
    )

    created = client.create_transfer_config(
        bigquery_datatransfer.CreateTransferConfigRequest(
            parent=parent,
            transfer_config=transfer_config,
        )
    )

    print("Scheduled query created.")
    print(f"  Name (resource) : {created.name}")
    print(f"  Display name    : {created.display_name}")
    print(f"  Schedule        : {created.schedule}")
    print(f"  Next run        : {created.next_run_time}")
    print(f"  Writes table    : {project_id}.{dataset_id}.daily_word_summary")
    print("\nCleanup tip: delete via console (BigQuery > Scheduled queries) or")
    print(f"  client.delete_transfer_config(name='{created.name}')")

    return created.name
