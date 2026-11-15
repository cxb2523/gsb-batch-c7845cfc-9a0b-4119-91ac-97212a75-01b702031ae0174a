# 40. Vector Search Index Pipeline

> End-to-end Vertex AI Vector Search pipeline: create a Tree-AH index with STREAM_UPDATE, upsert vectors, and query nearest neighbors. DRY_RUN=True by default — creating a deployed endpoint incurs real per-node-hour costs. **GCP Services:** Vertex AI Vector Search (Matching Engine)

## Architecture

```
main.py
  |
  |-- index_manager.py --> Vertex AI Vector Search API
  |     create_tree_ah_index()     Tree-AH index (STREAM_UPDATE, DOT_PRODUCT)
  |     MatchingEngineIndexEndpoint.create()
  |     endpoint.deploy_index()    <-- BILLING STARTS HERE
  |
  |-- vector_ops.py ----> Vertex AI Vector Search API
        index.upsert_datapoints()  Streaming insert (queryable in seconds)
        endpoint.find_neighbors()  k-NN query
              |
              v
        Ranked nearest-neighbor results (stdout)
```

## What this demonstrates

- Creating a Vertex AI Vector Search (Matching Engine) Tree-AH index with STREAM_UPDATE mode
- Deploying the index to a public endpoint
- Upserting datapoints (vectors) via streaming update — no batch rebuild required
- Running a nearest-neighbor query and reading the ranked distance results
- DRY_RUN pattern: safe default that prints the full plan without creating billable resources
- Cost-awareness: endpoint bills per-node-hour 24/7; always undeploy when idle
- Two-step pipeline: infrastructure (index/endpoint) and operations (upsert/query) are separated

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1

# Authenticate
gcloud auth application-default login
```

The running principal needs:

| Permission scope | Minimum role |
|---|---|
| Create/manage indexes and endpoints | `roles/aiplatform.user` |

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Safe dry run (default) — no resources created, no cost
python main.py

# Real run — creates billable resources
DRY_RUN=false python main.py

# Use pre-existing index + endpoint
INDEX_ID=<numeric-id> ENDPOINT_ID=<numeric-id> python main.py
```

## Cost Warning

A deployed Vector Search index endpoint bills **per node-hour, 24/7**, even with zero queries. The smallest configuration (1 shard) is roughly equivalent to an e2-standard-2 VM — approximately $60-80+/month. Always undeploy when you are done:

```python
endpoint.undeploy_index(deployed_index_id="demo_deployed_idx")
endpoint.delete()
index.delete()
```

## Files

| File | Purpose |
|---|---|
| `main.py` | Orchestrates the full pipeline: create index → upsert → query |
| `index_manager.py` | Index + endpoint creation and deployment; DRY_RUN guard |
| `vector_ops.py` | Vector upsert (streaming) and nearest-neighbor query |
| `deploy.sh` | Enables APIs, explains IAM, GCS staging, and cost warning |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
