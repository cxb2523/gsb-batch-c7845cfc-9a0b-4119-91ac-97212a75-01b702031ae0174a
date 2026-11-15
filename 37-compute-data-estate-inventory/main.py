# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 37 — Compute + Data Estate Inventory
Inventories Compute Engine resources (VMs + disks) then the data estate
(BigQuery + GCS), and prints a consolidated report with FinOps flags.
"""

import os

from compute_inventory import inventory_compute
from data_estate import fmt_gb, inventory_bigquery, inventory_gcs

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")


def print_consolidated_report(compute_stats: dict, bq_stats: dict, gcs_stats: dict) -> None:
    print("\n")
    print("=" * 80)
    print("CONSOLIDATED INVENTORY REPORT")
    print("=" * 80)

    # Compute summary
    inst_count = len(compute_stats.get("instances", []))
    disk_count = len(compute_stats.get("disks", []))
    stopped_cost_flags = compute_stats.get("stopped_with_disks", 0)

    print(f"\n[COMPUTE ENGINE]")
    print(f"  Instances      : {inst_count}")
    print(f"  Disks          : {disk_count}")
    if stopped_cost_flags:
        print(f"  Cost flags     : {stopped_cost_flags} TERMINATED VM(s) with attached disks still billing")
    else:
        print("  Cost flags     : None")

    # BigQuery summary
    print(f"\n[BIGQUERY]")
    print(f"  Datasets       : {bq_stats.get('datasets', 0)}")
    print(f"  Tables         : {bq_stats.get('tables', 0)}")
    print(f"  Logical size   : {fmt_gb(bq_stats.get('total_bytes', 0))}")

    # GCS summary
    no_lc = gcs_stats.get("no_lifecycle", 0)
    print(f"\n[CLOUD STORAGE]")
    print(f"  Buckets        : {gcs_stats.get('buckets', 0)}")
    if no_lc:
        print(f"  Cost flags     : {no_lc} bucket(s) with NO lifecycle rules — bytes won't auto-transition")
    else:
        print("  Cost flags     : All buckets have lifecycle rules")

    # Recommendations
    print("\n" + "-" * 80)
    recommendations = []
    if stopped_cost_flags:
        recommendations.append(
            "Snapshot + delete TERMINATED VMs — disk and static IP billing continue even when stopped."
        )
    if no_lc:
        recommendations.append(
            "Add age-based lifecycle rules to STANDARD-class buckets to auto-transition cold objects."
        )
    if bq_stats.get("tables", 0) > 0:
        recommendations.append(
            "Enable physical-storage billing on large BQ datasets — can reduce storage cost 50-80%."
        )

    if recommendations:
        print("RECOMMENDATIONS:")
        for i, r in enumerate(recommendations, 1):
            print(f"  {i}. {r}")
    else:
        print("No immediate FinOps flags. Re-run weekly to catch drift.")

    print("=" * 80)


def main():
    print("\n")
    print("#" * 80)
    print(f"  COMPUTE + DATA ESTATE INVENTORY — project: {PROJECT_ID}")
    print("#" * 80)

    # Phase 1: Compute Engine
    compute_stats = inventory_compute(PROJECT_ID)

    print("\n")

    # Phase 2: BigQuery
    bq_stats = inventory_bigquery(PROJECT_ID)

    # Phase 3: Cloud Storage
    gcs_stats = inventory_gcs(PROJECT_ID)

    print("\n" + "=" * 80)
    print("Next step: enable physical-storage billing checks on big BQ datasets,")
    print("and add lifecycle rules to buckets flagged NONE above.")

    # Consolidated report
    print_consolidated_report(compute_stats, bq_stats, gcs_stats)


if __name__ == "__main__":
    main()
