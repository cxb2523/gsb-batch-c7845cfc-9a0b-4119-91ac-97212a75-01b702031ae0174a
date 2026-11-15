"""
BigQuery dry-run cost estimation functions.

Uses dry_run=True to get bytes scanned WITHOUT executing the query, then
converts to an estimated on-demand cost. Wire into CI/PR checks to catch
expensive SQL before it hits production.
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

# FinOps: on-demand pricing (most regions) — $6.25 per TiB scanned.
# First 1 TiB/month is free, but estimate against list price to be safe.
PRICE_PER_TIB_USD = 6.25

QUERIES = {
    "Full scan (SELECT *)": """
        SELECT * FROM `bigquery-public-data.samples.shakespeare`
    """,
    "Column-pruned": """
        SELECT word, word_count FROM `bigquery-public-data.samples.shakespeare`
    """,
    "Aggregated (same scan as columns referenced)": """
        SELECT corpus, SUM(word_count) AS total
        FROM `bigquery-public-data.samples.shakespeare`
        GROUP BY corpus
    """,
}


def estimate_query_cost(
    client: bigquery.Client,
    name: str,
    sql: str,
    price_per_tib: float = PRICE_PER_TIB_USD,
) -> int:
    """
    Dry-run a query and print the estimated cost without executing it.

    dry_run=True: BigQuery plans the query and returns total_bytes_processed
    without running it. Dry runs are FREE and consume no slots.
    use_query_cache=False gives the worst-case (cache-miss) estimate.

    Args:
        client:        BigQuery client.
        name:          Human-readable label for the query.
        sql:           SQL query string to estimate.
        price_per_tib: On-demand price per TiB (default: $6.25).

    Returns:
        Estimated bytes that would be scanned.
    """
    job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    job = client.query(sql, job_config=job_config)

    bytes_scanned = job.total_bytes_processed
    tib = bytes_scanned / (1024**4)
    cost = tib * price_per_tib

    print(f"\n{name}")
    print(f"  Bytes scanned  : {bytes_scanned:,} ({bytes_scanned / (1024**2):.2f} MiB)")
    print(f"  Estimated cost : ${cost:.6f} (at ${price_per_tib}/TiB on-demand)")
    return bytes_scanned


def run_cost_estimates(project_id: str = PROJECT_ID) -> None:
    """
    Run dry-run cost estimates for all sample queries and print a comparison.

    Shows the bytes difference between SELECT * (full scan), column-pruned,
    and aggregated queries. Also prints the FinOps guardrail pattern using
    maximum_bytes_billed.

    Args:
        project_id: GCP project to bill any real queries to.
    """
    client = bigquery.Client(project=project_id)
    print(f"Project: {project_id}")
    print("=== Dry-run cost estimates (no query actually executes) ===")

    results = {
        name: estimate_query_cost(client, name, sql)
        for name, sql in QUERIES.items()
    }

    full = results["Full scan (SELECT *)"]
    pruned = results["Column-pruned"]
    if full:
        print(f"\nColumn pruning saves {(1 - pruned / full) * 100:.0f}% vs SELECT * here.")

    # FinOps guardrail pattern: refuse to run anything over a byte budget.
    # job_config = QueryJobConfig(maximum_bytes_billed=10 * 1024**3)  # 10 GiB cap
    print("\nGuardrail tip: set maximum_bytes_billed on real runs — the job")
    print("fails fast (and free) if it would exceed the cap.")
