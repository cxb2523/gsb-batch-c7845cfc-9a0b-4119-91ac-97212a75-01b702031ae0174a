# 6. BigQuery External Tables + AI Embeddings

> Query CSV data directly from GCS without loading it into BigQuery, then generate text embeddings in SQL via a Vertex AI remote model. **GCP Services:** BigQuery, Cloud Storage, Vertex AI (text-embedding-005), BigQuery Connections

## Architecture

```
data/sales_data.csv
        │
        ▼
  gsutil cp
        │
        ▼
gs://<bucket>/data/sales_data.csv
        │
        ▼ (external data config — no BQ storage)
┌───────────────────────┐
│  BigQuery             │
│  Dataset: your_dataset│
│  Table: sales_external│◄── ExternalConfig (CSV, autodetect)
│  (zero BQ storage)    │
└──────────┬────────────┘
           │ SELECT / GROUP BY
           ▼
     Query Results

        +

BQ Cloud Resource Connection
        │ (service account → Vertex AI)
        ▼
  ML.GENERATE_EMBEDDING
  (text-embedding-005)
        │
        ▼
  Embedding vectors (dims: 768)
```

## What this demonstrates

- Creating a BigQuery external table over a GCS CSV with `ExternalConfig` — zero BQ storage cost
- Autodetect schema from CSV headers at query time
- Querying an external table identically to a native table (GROUP BY, aggregations)
- Creating a BQ Cloud Resource connection that delegates auth to Vertex AI
- Running `ML.GENERATE_EMBEDDING` entirely in SQL — no Python ML code required
- Graceful degradation when the BQ connection is not yet set up

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GCS_BUCKET=your-gcs-bucket        # bucket must already exist
export BQ_DATASET=your_dataset           # optional, defaults to your_dataset
export BQ_CONNECTION_ID=vertex-ai-conn   # optional, name of BQ connection
```

Authenticate:
```bash
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

`deploy.sh` enables APIs, uploads the CSV to GCS, and prints the exact commands to create the BQ connection and grant the Vertex AI IAM binding.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

The script runs steps 1-3 (dataset, external table, query) immediately. Step 4 (embeddings) requires the BQ connection setup — it prints instructions and skips gracefully if not ready.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator: dataset → external table → query → embeddings |
| `external_table.py` | `create_external_table()` and `query_external_table()` |
| `ai_embeddings.py` | `create_remote_model()` and `generate_embeddings()` via ML.GENERATE_EMBEDDING |
| `data/sales_data.csv` | Sample GCP product order data (20 rows) |
| `deploy.sh` | API enablement, GCS upload, BQ connection setup instructions |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
