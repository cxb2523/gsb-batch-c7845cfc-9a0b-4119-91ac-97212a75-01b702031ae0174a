"""
BigQuery FinOps & Cost Visibility — entry point.

Runs three cost visibility demos:
  1. Dry-run cost estimates — bytes scanned and estimated $ without executing
  2. INFORMATION_SCHEMA FinOps audit — top queries + per-user spend last 7 days
  3. Scheduled query creation — daily aggregate via Data Transfer Service

Usage:
    python main.py
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os

from cost_estimate import run_cost_estimates
from finops_queries import run_finops_audit
from scheduled_query import create_scheduled_query

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

SEPARATOR = "=" * 60


def section(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def main():
    section("1. Dry-Run Cost Estimates (No Query Executes)")
    run_cost_estimates(project_id=PROJECT_ID)

    section("2. INFORMATION_SCHEMA FinOps Audit (Last 7 Days)")
    run_finops_audit(project_id=PROJECT_ID)

    section("3. Create Scheduled Query (Data Transfer Service)")
    print("Note: this creates a real DTS transfer config in your project.")
    print("It will run every 24 hours. Clean up via BQ Console after testing.\n")
    create_scheduled_query(project_id=PROJECT_ID)

    print(f"\n{SEPARATOR}")
    print("All FinOps demos complete.")


if __name__ == "__main__":
    main()
