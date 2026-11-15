# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Extract phase — CSV loading and pandas <-> BigQuery roundtrip demonstration.
Shows the correct library choice (google-cloud-bigquery client vs pandas-gbq)
and how to use load_table_from_dataframe for free batch loads.
"""

from pathlib import Path

import pandas as pd
from google.cloud import bigquery


def load_csv(csv_path: str | Path) -> pd.DataFrame:
    """Load the sales CSV into a typed DataFrame.

    Always pass dtype and parse_dates — inference on large files is slow and
    can silently mis-type columns (e.g. IDs with leading zeros -> int).

    Args:
        csv_path: Path to the source CSV file.

    Returns:
        Typed DataFrame with order_date as datetime64.
    """
    return pd.read_csv(
        csv_path,
        dtype={
            "order_id": "int64",
            "customer_name": "string",
            "region": "string",
            "product": "string",
            "category": "string",
            "quantity": "int64",
            "unit_price": "float64",
            "total_amount": "float64",
        },
        parse_dates=["order_date"],
    )


def roundtrip_to_bq(df: pd.DataFrame, project_id: str, table_id: str) -> int:
    """Load a DataFrame to BigQuery and query it back — demonstrates roundtrip.

    Library choice:
      - google-cloud-bigquery (this file): full control — job configs, schemas,
        clustering, load/extract/copy jobs. Production-grade.
        load_table_from_dataframe serializes via Parquet under the hood, so
        dtypes map cleanly and it's a FREE batch load job (no streaming insert cost).
      - pandas-gbq (df.to_gbq / pd.read_gbq): thin ergonomic wrapper, fine for
        notebooks and ad-hoc analysis. Now delegates to this same client.

    Cost: BQ batch load jobs are FREE — you pay for storage, not the load.
    Cost: SELECT query charges bytes scanned — always project only needed columns.

    Args:
        df: Source DataFrame.
        project_id: GCP project ID.
        table_id: Fully-qualified BQ table ID (project.dataset.table).

    Returns:
        Row count verified in BigQuery.
    """
    client = bigquery.Client(project=project_id)

    # Prepare: convert datetime64 to date (BigQuery DATE, not TIMESTAMP)
    df_bq = df.copy()
    df_bq["order_date"] = df_bq["order_date"].dt.date

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=[
            bigquery.SchemaField("order_id", "INT64"),
            bigquery.SchemaField("order_date", "DATE"),
        ],
    )
    load_job = client.load_table_from_dataframe(df_bq, table_id, job_config=job_config)
    load_job.result()
    table = client.get_table(table_id)
    print(f"  loaded {table.num_rows} rows -> {table_id}")

    # Query back: project only needed columns to minimize scanned bytes
    sql = f"""
        SELECT region,
               COUNT(*)          AS orders,
               SUM(total_amount) AS revenue
        FROM `{table_id}`
        GROUP BY region
        ORDER BY revenue DESC
    """
    result_df = client.query(sql).result().to_dataframe()
    print("\n  Revenue by region (from BigQuery):")
    print(result_df.round(2).to_string(index=False))

    total_rows = result_df["orders"].sum()
    assert total_rows == len(df), f"Roundtrip mismatch: {total_rows} vs {len(df)}"
    return int(total_rows)
