# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Index manager module: creates a Vertex AI Vector Search (Matching Engine)
Tree-AH index with STREAM_UPDATE and an index endpoint, then deploys the index.
This is the infrastructure step before upserting or querying vectors.

COST GATED: DRY_RUN=True by default (prints plan only).
Set DRY_RUN=false to actually create billable resources.
"""

import os

from google.cloud import aiplatform

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

INDEX_DISPLAY_NAME = "demo-vs-index"
ENDPOINT_DISPLAY_NAME = "demo-vs-endpoint"
DEPLOYED_INDEX_ID = "demo_deployed_idx"
DIMENSIONS = 8  # tiny demo dimension; real embeddings are 768/1408/3072


def create_index_and_endpoint(
    project_id: str = PROJECT_ID,
    location: str = LOCATION,
    dry_run: bool = DRY_RUN,
) -> dict:
    """
    Creates a Tree-AH index, an index endpoint, and deploys the index.

    With dry_run=True (default): prints what would happen, no resources created.
    With dry_run=False: creates real billable resources — see cost warning below.

    Returns a dict with:
      - 'index_id': str resource ID of the created index (or '' in dry run)
      - 'endpoint_id': str resource ID of the created endpoint (or '' in dry run)
      - 'dry_run': bool
    """
    print(f"Project : {project_id} | Location: {location} | DRY_RUN: {dry_run}")
    print("-" * 78)

    if dry_run:
        print("DRY RUN — nothing will be created. The real run would:")
        print()
        print("1. aiplatform.MatchingEngineIndex.create_tree_ah_index(")
        print(f"       display_name='{INDEX_DISPLAY_NAME}',")
        print(f"       dimensions={DIMENSIONS},")
        print("       approximate_neighbors_count=10,")
        print("       distance_measure_type='DOT_PRODUCT_DISTANCE',")
        print("       index_update_method='STREAM_UPDATE')")
        print("   -> Index itself is cheap (storage-based), takes ~30-60 min.")
        print()
        print("2. aiplatform.MatchingEngineIndexEndpoint.create(")
        print(f"       display_name='{ENDPOINT_DISPLAY_NAME}',")
        print("       public_endpoint_enabled=True)")
        print()
        print(f"3. endpoint.deploy_index(index=index, deployed_index_id='{DEPLOYED_INDEX_ID}')")
        print("   -> *** COST WARNING ***: a deployed index endpoint bills")
        print("      PER NODE-HOUR 24/7 even with ZERO queries (smallest shard")
        print("      ~ e2-standard-2 class, roughly $60-80+/month). Always")
        print("      undeploy when done.")
        print()
        print("Run with: DRY_RUN=false python main.py")
        print()
        print("Cleanup commands (after real run):")
        print("   endpoint.undeploy_index(deployed_index_id=DEPLOYED_INDEX_ID)")
        print("   endpoint.delete()")
        print("   index.delete()")
        return {"index_id": "", "endpoint_id": "", "dry_run": True}

    aiplatform.init(project=project_id, location=location)

    print(f"[1/3] Creating Tree-AH index '{INDEX_DISPLAY_NAME}' (30-60 min)...")
    index = aiplatform.MatchingEngineIndex.create_tree_ah_index(
        display_name=INDEX_DISPLAY_NAME,
        dimensions=DIMENSIONS,
        approximate_neighbors_count=10,
        distance_measure_type="DOT_PRODUCT_DISTANCE",
        index_update_method="STREAM_UPDATE",
        description="Demo vector search index",
    )
    print(f"      Index created: {index.resource_name}")

    print(f"[2/3] Creating index endpoint '{ENDPOINT_DISPLAY_NAME}'...")
    endpoint = aiplatform.MatchingEngineIndexEndpoint.create(
        display_name=ENDPOINT_DISPLAY_NAME,
        public_endpoint_enabled=True,
    )
    print(f"      Endpoint created: {endpoint.resource_name}")

    print("[3/3] Deploying index (BILLING STARTS NOW, per node-hour)...")
    endpoint.deploy_index(index=index, deployed_index_id=DEPLOYED_INDEX_ID)
    print("      Deployed.")

    index_id = index.resource_name.split("/")[-1]
    endpoint_id = endpoint.resource_name.split("/")[-1]

    print()
    print("Export these for the vector ops step:")
    print(f"   export INDEX_ID={index_id}")
    print(f"   export ENDPOINT_ID={endpoint_id}")
    print()
    print("CLEANUP when finished (stops billing):")
    print(f"   endpoint.undeploy_index(deployed_index_id='{DEPLOYED_INDEX_ID}')")
    print("   endpoint.delete(); index.delete()")

    return {"index_id": index_id, "endpoint_id": endpoint_id, "dry_run": False}
