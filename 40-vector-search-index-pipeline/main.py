# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 40 — Vector Search Index Pipeline
Full end-to-end pipeline:
  1. Create Vector Search index + endpoint (DRY_RUN=True by default)
  2. Upsert demo vectors via streaming update
  3. Query nearest neighbors

DRY_RUN=True by default. Creating a Vector Search index incurs real costs
when enabled — see index_manager.py for the cost warning.

Usage (dry run, safe):
  python main.py

Usage (real resources — COSTS MONEY):
  DRY_RUN=false python main.py

Usage (pre-existing index + endpoint):
  INDEX_ID=<id> ENDPOINT_ID=<id> python main.py
"""

import os

from index_manager import create_index_and_endpoint
from vector_ops import print_setup_help, query_nearest_neighbors, upsert_vectors

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

# Override via env vars when using a pre-existing index + endpoint
INDEX_ID = os.environ.get("INDEX_ID", "")
ENDPOINT_ID = os.environ.get("ENDPOINT_ID", "")
DEPLOYED_INDEX_ID = os.environ.get("DEPLOYED_INDEX_ID", "demo_deployed_idx")


def main():
    print("\n")
    print("#" * 80)
    print(f"  VECTOR SEARCH INDEX PIPELINE — project: {PROJECT_ID}")
    print(f"  DRY_RUN: {DRY_RUN}")
    print("#" * 80)
    print()

    # Phase 1: Index + endpoint creation
    index_result = create_index_and_endpoint(
        project_id=PROJECT_ID,
        location=LOCATION,
        dry_run=DRY_RUN,
    )

    # Resolve index/endpoint IDs: prefer env vars (pre-existing), then creation result
    index_id = INDEX_ID or index_result.get("index_id", "")
    endpoint_id = ENDPOINT_ID or index_result.get("endpoint_id", "")

    if index_result.get("dry_run"):
        # In dry-run mode, skip upsert + query unless env vars point at existing resources
        if not index_id or not endpoint_id:
            print()
            print("DRY RUN complete. No upsert or query performed.")
            print("To run against an existing index + endpoint:")
            print("  INDEX_ID=<id> ENDPOINT_ID=<id> python main.py")
            return

    print("\n")

    # Phase 2: Upsert vectors
    if not index_id or not endpoint_id:
        print_setup_help(PROJECT_ID, LOCATION)
        return

    upsert_ok = upsert_vectors(
        index_id=index_id,
        project_id=PROJECT_ID,
        location=LOCATION,
    )
    if not upsert_ok:
        print_setup_help(PROJECT_ID, LOCATION)
        return

    # Phase 3: Nearest-neighbor query
    results = query_nearest_neighbors(
        endpoint_id=endpoint_id,
        deployed_index_id=DEPLOYED_INDEX_ID,
        num_neighbors=3,
        project_id=PROJECT_ID,
        location=LOCATION,
    )

    print("\n")
    print("=" * 80)
    print("PIPELINE COMPLETE")
    print("=" * 80)
    print(f"  Index ID   : {index_id}")
    print(f"  Endpoint ID: {endpoint_id}")
    print(f"  Neighbors returned: {len(results)}")
    print()
    print("NEXT STEPS:")
    print("  - Replace DEMO_DATAPOINTS in vector_ops.py with real embedding vectors")
    print("  - Replace QUERY_VECTOR with your runtime query embedding")
    print("  - Scale DIMENSIONS to match your embedding model (768 / 1408 / 3072)")
    print("  - Undeploy the endpoint when not in use to stop per-node-hour billing")
    print("=" * 80)


if __name__ == "__main__":
    main()
