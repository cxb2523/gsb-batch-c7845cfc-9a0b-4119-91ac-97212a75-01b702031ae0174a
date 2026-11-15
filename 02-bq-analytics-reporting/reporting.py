"""
BigQuery reporting functions: query-to-DataFrame export and dataset/table metadata listing.

Covers the "BQ does the heavy lifting, pandas does the last-mile analysis" pattern
and project inventory / storage-cost audit via the BigQuery metadata API.
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")

DATAFRAME_QUERY = """
    SELECT
        corpus,
        COUNT(DISTINCT word) AS distinct_words,
        SUM(word_count)      AS total_words
    FROM `bigquery-public-data.samples.shakespeare`
    GROUP BY corpus
    ORDER BY total_words DESC
    LIMIT 20
"""


def query_to_dataframe(project_id: str = PROJECT_ID) -> None:
    """
    Run a BigQuery query and convert the result to a pandas DataFrame.

    to_dataframe() needs the `db-dtypes` package to map BigQuery types
    (DATE, TIME, NUMERIC, ...) onto pandas dtypes correctly. Typical pattern:
    BQ does the heavy lifting (aggregation at scale), pandas handles last-mile
    analysis (describe, plot, export).

    Args:
        project_id: GCP project to bill the query to.
    """
    client = bigquery.Client(project=project_id)

    print("Running query and converting to DataFrame ...\n")

    # to_dataframe() needs the `db-dtypes` package to map BigQuery types
    # (DATE, TIME, NUMERIC, ...) onto pandas dtypes correctly.
    df = client.query(DATAFRAME_QUERY).result().to_dataframe()

    print("=== DataFrame head ===")
    print(df.head(10).to_string(index=False))

    print("\n=== dtypes ===")
    print(df.dtypes)

    # Quick aggregation in pandas — typical "BQ does the heavy lifting,
    # pandas does the last-mile analysis" split.
    print("\n=== Quick aggregation (pandas) ===")
    print(f"Corpora returned        : {len(df)}")
    print(f"Total words (sum)       : {df['total_words'].sum():,}")
    print(f"Mean distinct words     : {df['distinct_words'].mean():,.0f}")
    top = df.iloc[0]
    print(f"Largest corpus          : {top['corpus']} ({top['total_words']:,} words)")


def list_datasets_tables_metadata(
    project_id: str = PROJECT_ID,
    dataset_id: str = DATASET_ID,
) -> None:
    """
    Walk the project: list datasets, tables, and print full table metadata.

    Useful for inventory scripts and storage-cost audits. list_tables()
    returns lightweight TableListItem objects; get_table() fetches the full
    resource including rows, bytes, schema, and timestamps.

    Args:
        project_id: GCP project to inspect.
        dataset_id: Dataset to list tables from and drill into metadata.
    """
    client = bigquery.Client(project=project_id)

    # --- Datasets in the project -------------------------------------------
    print(f"=== Datasets in {project_id} ===")
    datasets = list(client.list_datasets())  # cheap metadata call, no slots
    for ds in datasets:
        print(f"  - {ds.dataset_id}")
    print(f"  ({len(datasets)} total)")

    # --- Tables in the demo dataset ------------------------------------------
    print(f"\n=== Tables in {dataset_id} ===")
    tables = list(client.list_tables(f"{project_id}.{dataset_id}"))
    for t in tables:
        # list_tables returns lightweight TableListItem; type is included.
        print(f"  - {t.table_id:<30} ({t.table_type})")
    if not tables:
        print("  (none — run the load samples first)")
        return

    # --- Full metadata for each table -----------------------------------------
    # get_table() fetches the full resource: rows, bytes, schema, timestamps.
    print(f"\n=== Table metadata ===")
    for t in tables:
        table = client.get_table(t.reference)
        size_mb = (table.num_bytes or 0) / (1024 * 1024)
        print(f"\n{table.table_id}")
        print(f"  rows     : {table.num_rows:,}")
        print(f"  size     : {size_mb:.2f} MB")
        print(f"  created  : {table.created:%Y-%m-%d %H:%M:%S} UTC")
        print(f"  modified : {table.modified:%Y-%m-%d %H:%M:%S} UTC")
        if table.time_partitioning:
            print(
                f"  partition: {table.time_partitioning.type_} on "
                f"{table.time_partitioning.field}"
            )
        cols = ", ".join(f"{f.name}:{f.field_type}" for f in table.schema[:6])
        print(f"  schema   : {cols}{' ...' if len(table.schema) > 6 else ''}")
