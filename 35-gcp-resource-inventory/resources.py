# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
GCP Resource Lister — Cloud Asset Inventory search_all_resources.

Two functions:
  list_all_resources_in_project()  — enumerate every resource type in a project
  list_resources_org_filtered()    — org-wide search filtered by asset type and query

Cloud Asset Inventory is the ONLY service that sees everything in one call.
Per-service list APIs (compute, storage, bq...) require N clients and N
permission sets; CAI needs just roles/cloudasset.viewer on the scope.
Requires: gcloud services enable cloudasset.googleapis.com
"""

import os
from collections import Counter, defaultdict

from google.api_core import exceptions
from google.cloud import asset_v1

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
ORG_ID = os.environ.get("GCP_ORG_ID", "")

# Asset types worth tracking org-wide: VMs and buckets dominate spend in most
# orgs; BQ datasets dominate it in data-heavy ones. Add disks/addresses for
# orphan hunting.
TARGET_ASSET_TYPES = [
    "compute.googleapis.com/Instance",
    "storage.googleapis.com/Bucket",
    "bigquery.googleapis.com/Dataset",
]

# Query filter examples (CAI search syntax — combine with AND/OR):
#   location:us-central1       -> region pinning / data-residency audit
#   labels.env:prod            -> scope to an environment
#   NOT labels:cost-center     -> find UNLABELED resources (allocation gaps)
#   state:RUNNING              -> only live VMs
QUERY = ""  # empty = no filter; e.g. "NOT labels:env" to find unlabeled assets


def list_all_resources_in_project(project_id=PROJECT_ID):
    """
    Enumerates every resource in the specified project using Cloud Asset Inventory.

    Useful before migrations, security reviews, or FinOps cleanups to get a
    full picture of what exists.

    Args:
        project_id: GCP project ID to inventory.

    Returns:
        list: CAI ResourceSearchResult objects, or empty list on error.
    """
    client = asset_v1.AssetServiceClient()
    scope = f"projects/{project_id}"

    print("=" * 80)
    print(f"FULL RESOURCE INVENTORY — scope: {scope}")
    print("=" * 80)

    try:
        results = list(
            client.search_all_resources(
                request={"scope": scope, "page_size": 500}
            )
        )
    except exceptions.PermissionDenied:
        print("  Permission denied: needs roles/cloudasset.viewer on the project")
        print("  (or at org level for cross-project scopes), and the Cloud Asset")
        print("  API enabled in the project.")
        return []
    except exceptions.FailedPrecondition as e:
        print(f"  Failed precondition (is cloudasset.googleapis.com enabled?): {e.message}")
        return []

    # Group by asset_type — the count-per-type histogram is the fastest way to
    # spot sprawl (e.g. 400 firewall rules, 90 service accounts, orphan disks).
    type_counts = Counter(r.asset_type for r in results)
    samples = defaultdict(list)
    for r in results:
        if len(samples[r.asset_type]) < 3:
            samples[r.asset_type].append(r)

    print(f"\nTotal resources found: {len(results)}")
    print(f"Distinct asset types : {len(type_counts)}\n")

    print(f"{'COUNT':>6}  ASSET TYPE")
    print("-" * 70)
    for asset_type, count in type_counts.most_common():
        print(f"{count:>6}  {asset_type}")

    print("\nSAMPLE RESOURCES (top 5 types):")
    print("-" * 70)
    for asset_type, _ in type_counts.most_common(5):
        print(f"\n  {asset_type}:")
        for r in samples[asset_type]:
            loc = r.location or "global"
            # display_name can be empty for some types; fall back to full name
            name = r.display_name or r.name.rsplit("/", 1)[-1]
            print(f"    - {name}  [{loc}]  state={r.state or 'n/a'}")

    if not results:
        print("\n  No resources found — empty project or CAI hasn't indexed it yet.")

    return results


def _search_scope(client, scope, asset_types, query, note):
    """
    Internal helper: runs search_all_resources on one scope and prints results.

    Args:
        client:      asset_v1.AssetServiceClient instance.
        scope:       CAI scope string (e.g. 'organizations/NNN' or 'projects/ID').
        asset_types: List of asset type strings to filter.
        query:       CAI query filter string (empty = no filter).
        note:        Label printed next to the scope for readability.

    Returns:
        list or None: results list on success, None on permission/arg error.
    """
    print(f"\nScope: {scope}  {note}")
    print("-" * 75)
    try:
        results = list(
            client.search_all_resources(
                request={
                    "scope": scope,
                    "asset_types": asset_types,
                    "query": query,
                    "page_size": 500,
                }
            )
        )
    except exceptions.PermissionDenied:
        print("  Permission denied: needs roles/cloudasset.viewer at the ORG level")
        print("  for org scopes (project-level grants do NOT roll up).")
        return None
    except exceptions.InvalidArgument as e:
        print(f"  Invalid argument (bad org ID or query syntax?): {e.message}")
        return None

    by_type = {}
    for r in results:
        by_type.setdefault(r.asset_type, []).append(r)

    for asset_type in asset_types:
        items = by_type.get(asset_type, [])
        print(f"\n  {asset_type}  ({len(items)} found)")
        for r in items[:10]:
            name = r.display_name or r.name.rsplit("/", 1)[-1]
            proj = r.project.rsplit("/", 1)[-1] if r.project else "?"
            print(f"    - {name}  project={proj}  location={r.location or 'global'}")
        if len(items) > 10:
            print(f"    ... and {len(items) - 10} more")

    print(f"\n  Total matched: {len(results)}")
    return results


def list_resources_org_filtered(
    org_id=ORG_ID,
    project_id=PROJECT_ID,
    asset_types=None,
    query=QUERY,
):
    """
    Org-wide (or project-fallback) filtered resource search via CAI.

    Attempts org scope first if org_id is set; falls back to project scope.
    This is how you answer "show me every VM / bucket / BQ dataset across
    the company" in a single API call.

    Args:
        org_id:      GCP organization numeric ID (e.g. "123456789"). Optional.
        project_id:  GCP project ID used as fallback scope.
        asset_types: List of asset type strings. Defaults to TARGET_ASSET_TYPES.
        query:       CAI search filter string.

    Returns:
        list or None: matched CAI ResourceSearchResult objects.
    """
    if asset_types is None:
        asset_types = TARGET_ASSET_TYPES

    client = asset_v1.AssetServiceClient()
    print("=" * 80)
    print("ORG-WIDE FILTERED RESOURCE SEARCH")
    print("=" * 80)

    if org_id:
        result = _search_scope(
            client, f"organizations/{org_id}", asset_types, query, "(org-wide)"
        )
        if result is not None:
            return result
        print("\nFalling back to project scope...")
    else:
        print("\nNOTE: GCP_ORG_ID not set — falling back to single-project scope.")
        print("  Set GCP_ORG_ID to your numeric org ID for the real org-wide view.")
        print("  Find it with: gcloud organizations list")

    return _search_scope(
        client, f"projects/{project_id}", asset_types, query, "(project fallback)"
    )
