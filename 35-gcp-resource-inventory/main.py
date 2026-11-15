# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
GCP Resource Inventory — entry point.

Runs three inventory passes in sequence:
  1. List all projects visible to the caller (Resource Manager v3)
  2. Full resource inventory for the target project (Cloud Asset Inventory)
  3. Org-wide filtered resource search by asset type (CAI, falls back to project)

This is the foundation of any GCP governance, migration planning, or FinOps
baseline exercise.
"""

import os

from projects import list_all_projects, print_projects
from resources import list_all_resources_in_project, list_resources_org_filtered

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
ORG_ID = os.environ.get("GCP_ORG_ID", "")


def main():
    print("=" * 65)
    print("  GCP DATA ENGINEERING HUB — Project 35")
    print("  GCP Resource Inventory")
    print("=" * 65)
    print(f"\n  Project : {PROJECT_ID}")
    print(f"  Org ID  : {ORG_ID or '(not set — org-wide pass will use project fallback)'}")

    # ── Step 1: Project inventory ────────────────────────────────────
    print("\n\n[STEP 1] LIST ALL PROJECTS (caller-visible scope)")
    print("-" * 65)
    projects = list_all_projects()
    print_projects(projects)

    # ── Step 2: Full resource inventory for the target project ───────
    print("\n\n[STEP 2] FULL RESOURCE INVENTORY FOR PROJECT")
    print("-" * 65)
    all_resources = list_all_resources_in_project(project_id=PROJECT_ID)

    # ── Step 3: Org-wide filtered search ─────────────────────────────
    print("\n\n[STEP 3] ORG-WIDE FILTERED RESOURCE SEARCH")
    print("  (VMs, GCS Buckets, BigQuery Datasets)")
    print("-" * 65)
    filtered_resources = list_resources_org_filtered(
        org_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    # ── Summary ──────────────────────────────────────────────────────
    print("\n\n" + "=" * 65)
    print(f"  Projects visible        : {len(projects)}")
    print(f"  Total project resources : {len(all_resources) if all_resources else 0}")
    print(f"  Filtered (org/project)  : {len(filtered_resources) if filtered_resources else 0}")
    print("=" * 65)


if __name__ == "__main__":
    main()
