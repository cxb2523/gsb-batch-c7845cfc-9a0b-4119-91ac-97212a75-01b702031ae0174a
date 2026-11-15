"""
BigQuery FinOps audit functions via INFORMATION_SCHEMA.JOBS_BY_PROJECT.

Queries the last 7 days of job metadata: top queries by bytes billed,
slot-hours, and per-user spend. This is the raw data behind every BigQuery
cost dashboard — free to inspect with bigquery.jobs.listAll permission.
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

# JOBS_BY_PROJECT is regional — `region-us` matches the demo dataset location.
# Requires bigquery.jobs.listAll on the project (e.g. BigQuery Resource Viewer).
PRICE_PER_TIB_USD = 6.25  # on-demand list price


def _jobs_view(project_id: str) -> str:
    """Return the INFORMATION_SCHEMA.JOBS_BY_PROJECT view reference for the project."""
    return f"`{project_id}.region-us.INFORMATION_SCHEMA.JOBS_BY_PROJECT`"


def top_queries_by_bytes(client: bigquery.Client, project_id: str = PROJECT_ID) -> None:
    """
    Print the top 10 queries by bytes billed over the last 7 days.

    Shows user_email, job_id, bytes billed, slot-hours, estimated on-demand
    cost, and a truncated query preview. Slot-hours is the key metric under
    BigQuery Editions (capacity) pricing.

    Args:
        client:     BigQuery client.
        project_id: Project to query INFORMATION_SCHEMA for.
    """
    jobs_view = _jobs_view(project_id)
    sql = f"""
        SELECT
            user_email,
            job_id,
            total_bytes_billed,
            -- slot-hours = total_slot_ms / 1000 / 3600; the real "compute" metric
            -- and what matters under capacity (editions) pricing.
            ROUND(total_slot_ms / 3600000, 2) AS slot_hours,
            ROUND(total_bytes_billed / POW(1024, 4) * {PRICE_PER_TIB_USD}, 4) AS est_cost_usd,
            LEFT(REGEXP_REPLACE(query, r'\\s+', ' '), 80) AS query_preview
        FROM {jobs_view}
        WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
          AND job_type = 'QUERY'
          AND state = 'DONE'
          AND total_bytes_billed IS NOT NULL
        ORDER BY total_bytes_billed DESC
        LIMIT 10
    """

    print(f"=== Top 10 queries by bytes billed — last 7 days ({project_id}) ===")
    for row in client.query(sql).result():
        print(f"\n  user       : {row.user_email}")
        print(
            f"  bytes      : {row.total_bytes_billed:,}  "
            f"slot-hrs: {row.slot_hours}  est: ${row.est_cost_usd}"
        )
        print(f"  query      : {row.query_preview}")


def per_user_spend(client: bigquery.Client, project_id: str = PROJECT_ID) -> None:
    """
    Print per-user spend summary over the last 7 days.

    Aggregates query count, GiB billed, slot-hours, and estimated on-demand
    cost per user. COALESCE handles cached/metadata-only jobs that have
    NULL bytes and slots.

    Args:
        client:     BigQuery client.
        project_id: Project to query INFORMATION_SCHEMA for.
    """
    jobs_view = _jobs_view(project_id)
    sql = f"""
        SELECT
            user_email,
            COUNT(*) AS query_count,
            -- COALESCE: cached/metadata-only jobs have NULL bytes/slots and would
            -- otherwise break the aggregate formatting downstream
            ROUND(SUM(COALESCE(total_bytes_billed, 0)) / POW(1024, 3), 2) AS gib_billed,
            ROUND(SUM(COALESCE(total_slot_ms, 0)) / 3600000, 2) AS slot_hours,
            ROUND(SUM(COALESCE(total_bytes_billed, 0)) / POW(1024, 4) * {PRICE_PER_TIB_USD}, 4) AS est_cost_usd
        FROM {jobs_view}
        WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
          AND job_type = 'QUERY' AND state = 'DONE'
        GROUP BY user_email
        ORDER BY est_cost_usd DESC
    """

    print("\n=== Per-user spend — last 7 days ===")
    print(f"{'USER':<40} {'QUERIES':>8} {'GiB':>10} {'SLOT-HRS':>9} {'EST $':>9}")
    for row in client.query(sql).result():
        print(
            f"{row.user_email:<40} {row.query_count:>8} {row.gib_billed:>10} "
            f"{row.slot_hours:>9} {row.est_cost_usd:>9}"
        )

    print("\nFinOps notes: cached hits bill 0 bytes; under editions pricing,")
    print("slot_hours (not bytes) is the column that maps to your bill.")


def run_finops_audit(project_id: str = PROJECT_ID) -> None:
    """
    Run both FinOps INFORMATION_SCHEMA queries against the given project.

    Args:
        project_id: GCP project to audit.
    """
    client = bigquery.Client(project=project_id)
    top_queries_by_bytes(client, project_id)
    per_user_spend(client, project_id)
