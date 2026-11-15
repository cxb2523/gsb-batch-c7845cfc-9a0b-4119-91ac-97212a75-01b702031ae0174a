# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 38 — Billing + Cost Recommendations
Lists billing accounts and project billing linkage, optionally queries the
BigQuery billing export for the last 30 days of cost by service, then sweeps
the Recommender API for actionable cost-saving recommendations.
"""

import os

from billing import list_billing_accounts, query_billing_export
from recommendations import fetch_cost_recommendations

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
BILLING_EXPORT_TABLE = os.environ.get("BILLING_EXPORT_TABLE", "")


def print_finops_summary(billing_info: dict, recs_summary: dict) -> None:
    print("\n")
    print("=" * 80)
    print("FINOPS SUMMARY")
    print("=" * 80)

    # Billing status
    if billing_info.get("billing_enabled"):
        print(f"  Billing account : {billing_info.get('billing_account', 'unknown')}")
        print(f"  Status          : ENABLED")
    else:
        print("  Status          : BILLING DISABLED — action required")

    # Export status
    if BILLING_EXPORT_TABLE:
        print(f"  Export table    : {BILLING_EXPORT_TABLE}")
    else:
        print("  Export table    : NOT SET — set BILLING_EXPORT_TABLE for cost queries")

    # Recommender summary
    found = recs_summary.get("found", 0)
    savings = recs_summary.get("total_savings", {})
    print(f"  Recommendations : {found} found")
    for currency, amount in savings.items():
        label = "savings" if amount < 0 else "additional cost"
        print(f"  Est. impact     : {amount:.2f} {currency} ({label})")

    print("\n" + "-" * 80)
    if found > 0:
        print("ACTION: Review recommendations above — apply idle-VM deletions and")
        print("rightsizing first (highest confidence, instant savings).")
    else:
        print("No active recommendations found.")
        print("Tip: Recommenders need 8-14 days of usage telemetry to generate findings.")
    print("=" * 80)


def main():
    print("\n")
    print("#" * 80)
    print(f"  BILLING + COST RECOMMENDATIONS — project: {PROJECT_ID}")
    print("#" * 80)

    print("=" * 80)
    print("BILLING ACCOUNTS + EXPORT REPORT")
    print("=" * 80)

    # Phase 1: Billing accounts + linkage
    billing_info = list_billing_accounts(PROJECT_ID)

    # Phase 2: Billing export query (optional)
    if BILLING_EXPORT_TABLE:
        query_billing_export(BILLING_EXPORT_TABLE, PROJECT_ID)
    else:
        print("\nBILLING_EXPORT_TABLE not set — skipping cost query.")
        print("-" * 75)
        print("Set up billing export (the #1 FinOps prerequisite):")
        print("  1. Console -> Billing -> Billing export -> BigQuery export")
        print("  2. Enable 'Detailed usage cost' export into a dedicated dataset")
        print("     (detailed includes resource-level rows; standard does not).")
        print("  3. Export starts from enablement day — it does NOT backfill,")
        print("     so enable it on day one of every org. Then:")
        print("     export BILLING_EXPORT_TABLE=proj.dataset.gcp_billing_export_resource_v1_XXXX")

    print("\n")

    # Phase 3: Recommender API cost savings
    recs_summary = fetch_cost_recommendations(PROJECT_ID)

    # FinOps summary
    print_finops_summary(billing_info, recs_summary)


if __name__ == "__main__":
    main()
