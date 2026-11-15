# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Apache Beam CSV → BigQuery Pipeline

Reads sales_data.csv, parses each row into a typed dict matching the BigQuery
schema, and loads it via WriteToBigQuery using the FILE_LOADS method.

Even on DirectRunner this performs a REAL BigQuery load job — your project,
your table, real data. Ensure the dataset exists before running (deploy.sh
creates it, or see the Setup section in README.md).

WriteToBigQuery mechanics:
  - FILE_LOADS method: stages newline-delimited JSON to GCS, then triggers a
    BigQuery load job. DirectRunner cannot always infer where to stage temp
    files, so GCS_TEMP must be set explicitly — without it you can hit a
    "no temp location" error locally.
  - CREATE_IF_NEEDED: creates the target table from the schema string on first
    run. The dataset must already exist.
  - WRITE_TRUNCATE: idempotent re-runs — replaces table contents each time.
    Use WRITE_APPEND for incremental loads (deduplication is then your problem).
"""

import csv
import io
import os
from pathlib import Path

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions

from schema import BQ_SCHEMA, CSV_COLUMNS

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")

DEFAULT_CSV = Path(__file__).resolve().parent / "data" / "sales_data.csv"
BQ_DATASET = "your_dataset"
BQ_TABLE_NAME = "beam_sales"


def get_bq_table(project_id: str, dataset: str = BQ_DATASET, table: str = BQ_TABLE_NAME) -> str:
    """Return the fully qualified BigQuery table reference (PROJECT:DATASET.TABLE)."""
    return f"{project_id}:{dataset}.{table}"


def get_gcs_temp(gcs_bucket: str) -> str:
    """Return the GCS temp location for WriteToBigQuery FILE_LOADS staging."""
    return f"gs://{gcs_bucket}/beam-tmp/"


def parse_csv_line(line: str) -> dict:
    """Parse one CSV line into a dict whose keys match the BigQuery schema.

    Uses csv.reader — NOT line.split(",") — to handle quoted fields correctly
    (e.g., customer_name = "Acme, Inc." would break a naive split).
    """
    row = next(csv.reader(io.StringIO(line)))
    record = dict(zip(CSV_COLUMNS, row))
    record["quantity"] = int(record["quantity"])
    record["unit_price"] = float(record["unit_price"])
    record["total_amount"] = float(record["total_amount"])
    return record


def run_pipeline(
    csv_path: str = str(DEFAULT_CSV),
    project_id: str = PROJECT_ID,
    gcs_bucket: str = GCS_BUCKET,
    dataset: str = BQ_DATASET,
    table: str = BQ_TABLE_NAME,
) -> None:
    """Run the CSV → BigQuery Beam pipeline.

    On DirectRunner this still performs a REAL BigQuery load job.
    To switch to DataflowRunner, update options below and set csv_path to gs://.
    """
    bq_table = get_bq_table(project_id, dataset, table)
    gcs_temp = get_gcs_temp(gcs_bucket)

    print("=" * 70)
    print("Beam CSV → BigQuery load")
    print(f"Input  : {csv_path}")
    print(f"Table  : {bq_table}")
    print(f"GCS tmp: {gcs_temp}")
    print("=" * 70)

    # When does Beam earn its place over `bq load`?
    # - Pure CSV → BQ with NO transforms: use `bq load` or LOAD DATA (simpler).
    # - Per-row transforms, validation, joins, reuse in batch + streaming → Beam.
    # This sample keeps the transform trivial so WriteToBigQuery mechanics stay in focus.

    # DirectRunner still runs a real load: stages JSON to gcs_temp, triggers BQ load.
    # To run on Dataflow:
    #   options = PipelineOptions(
    #       runner="DataflowRunner",
    #       project=project_id,
    #       region=REGION,
    #       temp_location=gcs_temp,
    #   )
    options = PipelineOptions(runner="DirectRunner", project=project_id)

    with beam.Pipeline(options=options) as pipeline:
        (
            pipeline
            | "ReadCSV" >> beam.io.ReadFromText(csv_path, skip_header_lines=1)
            | "ParseRows" >> beam.Map(parse_csv_line)
            | "WriteToBigQuery" >> beam.io.WriteToBigQuery(
                bq_table,
                schema=BQ_SCHEMA,
                create_disposition=beam.io.BigQueryDisposition.CREATE_IF_NEEDED,
                write_disposition=beam.io.BigQueryDisposition.WRITE_TRUNCATE,
                custom_gcs_temp_location=gcs_temp,
            )
        )

    print("\nLoad complete (real BigQuery load job, even on DirectRunner).")
    print(f"Verify: bq head -n 5 {bq_table.replace(':', '.')}")
    print("Cost note: load jobs are free; storage + later queries are billed per TB scanned.")


if __name__ == "__main__":
    run_pipeline()
