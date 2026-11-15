"""
BigQuery query functions: simple queries and parameterized queries.

Covers the "hello world" client setup verification all the way through
named query parameters — the two foundational query patterns for any BQ project.
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

SIMPLE_QUERY = """
    SELECT
        word,
        SUM(word_count) AS total_count
    FROM `bigquery-public-data.samples.shakespeare`
    WHERE LENGTH(word) > 4          -- skip short filler words
    GROUP BY word
    ORDER BY total_count DESC
    LIMIT 10
"""

# Named parameters use @name syntax — never f-string user input into SQL.
PARAMETERIZED_QUERY = """
    SELECT
        word,
        SUM(word_count) AS total_count
    FROM `bigquery-public-data.samples.shakespeare`
    WHERE LENGTH(word) >= @min_length
      AND corpus = @corpus
    GROUP BY word
    ORDER BY total_count DESC
    LIMIT @row_limit
"""


def run_simple_query(project_id: str = PROJECT_ID) -> None:
    """
    Run a plain SQL query against the Shakespeare public dataset.

    The "hello world" of BigQuery — verifies auth and client setup.
    Prints the top 10 words (length > 4) by total count, plus job stats.

    Args:
        project_id: GCP project to bill the query to.
    """
    client = bigquery.Client(project=project_id)

    print(f"Project : {project_id}")
    print("Query   : Top 10 words (len > 4) in Shakespeare's works\n")

    # client.query() submits the job; calling .result() waits for completion
    # and returns a RowIterator.
    query_job = client.query(SIMPLE_QUERY)
    rows = query_job.result()

    print(f"{'WORD':<15} {'TOTAL_COUNT':>12}")
    print("-" * 28)
    for row in rows:
        # Rows support both index and attribute/key access.
        print(f"{row['word']:<15} {row['total_count']:>12,}")

    # Job stats are available after completion — handy for quick cost checks.
    print(f"\nBytes processed : {query_job.total_bytes_processed:,}")
    print(f"Cache hit       : {query_job.cache_hit}")
    print("Done.")


def run_parameterized_query(
    project_id: str = PROJECT_ID,
    min_length: int = 6,
    corpus: str = "hamlet",
    row_limit: int = 10,
) -> None:
    """
    Run a query with named BigQuery query parameters.

    Parameters prevent SQL injection and let you reuse one query template safely.
    Each parameter declares its BigQuery type explicitly, making the query plan
    cacheable and the values injection-safe.

    Args:
        project_id:  GCP project to bill the query to.
        min_length:  Minimum word length to filter on.
        corpus:      Shakespeare corpus name to filter on (e.g. "hamlet").
        row_limit:   Maximum number of rows to return.
    """
    client = bigquery.Client(project=project_id)

    # Each parameter declares its BigQuery type explicitly. This is what makes
    # the query plan cacheable and the values injection-safe.
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("min_length", "INT64", min_length),
            bigquery.ScalarQueryParameter("corpus", "STRING", corpus),
            bigquery.ScalarQueryParameter("row_limit", "INT64", row_limit),
        ]
    )

    print(f"Project    : {project_id}")
    print(f"Parameters : min_length={min_length}, corpus='{corpus}', limit={row_limit}\n")

    rows = client.query(PARAMETERIZED_QUERY, job_config=job_config).result()

    print(f"{'WORD':<18} {'TOTAL_COUNT':>12}")
    print("-" * 31)
    for row in rows:
        print(f"{row.word:<18} {row.total_count:>12,}")

    print("\nDone. Change the parameter values above — the SQL never changes.")
