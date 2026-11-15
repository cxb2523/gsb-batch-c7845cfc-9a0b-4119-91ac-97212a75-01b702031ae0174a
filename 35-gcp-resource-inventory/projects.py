# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
GCP Project Lister — uses Resource Manager v3 search_projects to enumerate
every project visible to the caller, with id/name/state/labels/parent.

First step in any org-wide inventory or FinOps exercise — you can't govern
what you can't enumerate.
"""

import os

from google.api_core import exceptions
from google.cloud import resourcemanager_v3

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")


def list_all_projects():
    """
    Lists all projects visible to the authenticated caller.

    Scope depends on WHERE IAM bindings live:
      - org-level Viewer/Browser  -> full org visibility (best for FinOps audits)
      - folder-level binding      -> only projects under that folder
      - per-project bindings      -> only those individual projects

    If the list looks short, the caller identity is likely project-scoped.
    Grant roles/browser at the ORG node for a true inventory baseline.

    Returns:
        list[resourcemanager_v3.Project]: visible project objects,
        or an empty list on permission error.
    """
    client = resourcemanager_v3.ProjectsClient()

    try:
        projects = list(client.search_projects(request={"query": ""}))
    except exceptions.PermissionDenied:
        print("  Permission denied: needs roles/browser (or roles/viewer) —")
        print("  ideally granted at the ORG level to cover all projects.")
        return []

    return projects


def print_projects(projects):
    """
    Prints a formatted project inventory report to stdout.

    Args:
        projects: List of resourcemanager_v3.Project objects.
    """
    print("=" * 80)
    print("PROJECT INVENTORY (search_projects — caller-visible scope)")
    print("=" * 80)

    active = 0
    for p in projects:
        state = p.state.name  # ACTIVE vs DELETE_REQUESTED
        if state == "ACTIVE":
            # Soft-deleted projects still appear for ~30 days and can hide
            # spend history — only count truly active ones.
            active += 1
        labels = (
            ", ".join(f"{k}={v}" for k, v in p.labels.items()) or "(no labels)"
        )
        print(f"\n  Project ID : {p.project_id}")
        print(f"  Name       : {p.display_name}")
        print(f"  State      : {state}")
        print(f"  Parent     : {p.parent}")  # folders/NNN or organizations/NNN
        # Labels are the backbone of cost allocation: without team/env/cost-center
        # labels, billing export rows can't be attributed and showback fails.
        print(f"  Labels     : {labels}")

    print("\n" + "-" * 80)
    print(f"Total projects visible: {len(projects)} ({active} ACTIVE)")
    if not projects:
        print(f"  None visible. Check scope: gcloud projects describe {PROJECT_ID}")
