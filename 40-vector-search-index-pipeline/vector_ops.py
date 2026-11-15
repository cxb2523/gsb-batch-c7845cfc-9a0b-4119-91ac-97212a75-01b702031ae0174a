# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Vector operations module: streams (upserts) demo vectors into an existing
Vector Search index via STREAM_UPDATE, then runs a nearest-neighbor query
against the deployed endpoint. Pairs with index_manager.py.

Requires an existing index + deployed endpoint (created by index_manager.py
with DRY_RUN=false, or pre-existing resources).
"""

import os

from google.cloud import aiplatform

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
INDEX_ID = os.environ.get("INDEX_ID", "")
ENDPOINT_ID = os.environ.get("ENDPOINT_ID", "")
DEPLOYED_INDEX_ID = os.environ.get("DEPLOYED_INDEX_ID", "demo_deployed_idx")

# Tiny 8-dim demo vectors (matches DIMENSIONS=8 in index_manager.py).
DEMO_DATAPOINTS = {
    "doc-cloud-run":  [0.9, 0.1, 0.0, 0.2, 0.1, 0.0, 0.3, 0.1],
    "doc-bigquery":   [0.1, 0.9, 0.2, 0.0, 0.1, 0.3, 0.0, 0.1],
    "doc-vertex-ai":  [0.2, 0.1, 0.9, 0.1, 0.0, 0.1, 0.2, 0.0],
    "doc-gke":        [0.8, 0.2, 0.1, 0.3, 0.2, 0.0, 0.1, 0.1],
    "doc-finops":     [0.0, 0.8, 0.3, 0.1, 0.2, 0.4, 0.0, 0.2],
}
QUERY_VECTOR = [0.85, 0.15, 0.05, 0.25, 0.15, 0.0, 0.2, 0.1]  # ~ "cloud run"


def print_setup_help(project_id: str, location: str) -> None:
    print("Missing INDEX_ID and/or ENDPOINT_ID environment variables.")
    print()
    print("Setup:")
    print("  1. Run main.py with DRY_RUN=false to create index + endpoint,")
    print("     OR find existing resources with:")
    print(f"       gcloud ai indexes list --region={location} --project={project_id}")
    print(f"       gcloud ai index-endpoints list --region={location} --project={project_id}")
    print("  2. Export the numeric resource IDs:")
    print("       export INDEX_ID=1234567890123456789")
    print("       export ENDPOINT_ID=9876543210987654321")
    print("  3. Re-run: python main.py")


def upsert_vectors(
    index_id: str,
    project_id: str = PROJECT_ID,
    location: str = LOCATION,
    datapoints: dict = None,
) -> bool:
    """
    Upserts demo datapoints into the index via streaming update.

    Returns True on success, False if index_id is missing.
    """
    if not index_id:
        return False

    if datapoints is None:
        datapoints = DEMO_DATAPOINTS

    aiplatform.init(project=project_id, location=location)
    index = aiplatform.MatchingEngineIndex(index_name=index_id)
    dp_list = [
        {"datapoint_id": dp_id, "feature_vector": vec}
        for dp_id, vec in datapoints.items()
    ]
    print(f"[1/2] Upserting {len(dp_list)} datapoints into index {index_id}...")
    index.upsert_datapoints(datapoints=dp_list)
    print("      Upsert complete (streaming updates are queryable in seconds).")
    return True


def query_nearest_neighbors(
    endpoint_id: str,
    deployed_index_id: str = DEPLOYED_INDEX_ID,
    query_vector: list = None,
    num_neighbors: int = 3,
    project_id: str = PROJECT_ID,
    location: str = LOCATION,
) -> list:
    """
    Queries the deployed index endpoint for nearest neighbors.

    Returns a list of (rank, datapoint_id, distance) tuples.
    """
    if not endpoint_id:
        return []

    if query_vector is None:
        query_vector = QUERY_VECTOR

    aiplatform.init(project=project_id, location=location)
    endpoint = aiplatform.MatchingEngineIndexEndpoint(
        index_endpoint_name=endpoint_id
    )
    print(f"[2/2] Querying nearest neighbors (deployed_index_id={deployed_index_id})...")
    response = endpoint.find_neighbors(
        deployed_index_id=deployed_index_id,
        queries=[query_vector],
        num_neighbors=num_neighbors,
    )

    results = []
    print()
    print(f"{'RANK':<6} {'DATAPOINT ID':<20} {'DISTANCE':<12}")
    print("=" * 40)
    for rank, neighbor in enumerate(response[0], start=1):
        print(f"{rank:<6} {neighbor.id:<20} {neighbor.distance:<12.4f}")
        results.append((rank, neighbor.id, neighbor.distance))
    print("=" * 40)
    print()
    print("Reminder: the deployed index endpoint bills per node-hour even when")
    print("idle — undeploy it (see index_manager.py cleanup) when you are done.")
    return results
