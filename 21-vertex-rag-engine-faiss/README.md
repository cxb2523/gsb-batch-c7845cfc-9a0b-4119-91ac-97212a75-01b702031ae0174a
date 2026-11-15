# 21. Vertex RAG Engine + FAISS + BigQuery VECTOR_SEARCH

> Three RAG retrieval backends on GCP — from fully managed corpus to local FAISS index to SQL-native BigQuery VECTOR_SEARCH — in a single runnable project. **GCP Services:** Vertex AI RAG Engine, Gemini Embeddings, BigQuery, Cloud Storage

## Architecture

```
data/documents.txt
        |
        v
  [Chunk Splitter]
        |
        +----> [1] Vertex AI RAG Engine ---------> Managed Corpus (Spanner backend)
        |              |                                    |
        |         RAG Engine                        Gemini (tool grounding)
        |                                                   |
        +----> [2] Gemini Embeddings API ---------> FAISS IndexFlatIP (in-process)
        |              |                                    |
        |         L2-normalize                      inner-product search
        |                                                   |
        +----> [3] Gemini Embeddings API ---------> BigQuery Table (ARRAY<FLOAT64>)
                       |                                    |
                  output_dimensionality=768          VECTOR_SEARCH SQL function
                                                           |
                                              [Gemini] Grounded Answer
```

## What this demonstrates

- Vertex AI RAG Engine: zero-pipeline managed RAG — create a corpus, import a GCS document, retrieve and generate with a single Gemini tool call
- FAISS IndexFlatIP: in-process exact nearest-neighbor search with L2-normalized embeddings; ideal for prototyping and batch jobs where corpus fits in RAM
- BigQuery VECTOR_SEARCH: SQL-native vector retrieval co-located with analytics data; scales to billions of rows with a VECTOR INDEX for ANN
- Side-by-side comparison of managed vs. self-managed vs. warehouse-native vector retrieval trade-offs

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
# For Vertex RAG Engine (default region, no allowlist required):
export RAG_ENGINE_LOCATION=europe-west4
```

Authenticate:
```bash
gcloud auth application-default login
```

> **Vertex RAG Engine billing note:** `python main.py vertex` (or `python main.py` with no args) creates a managed corpus in your project. The corpus is deleted at the end of the run. Verify via Cloud Console > Vertex AI > RAG Engine if the script fails midway.

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run FAISS + BigQuery only (no managed corpus billing):
python main.py faiss bq

# Run all three backends:
python main.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrates all three RAG demos; accepts `vertex`, `faiss`, `bq` as CLI args |
| `vertex_rag.py` | Vertex AI RAG Engine — managed corpus, import, retrieval, Gemini grounding |
| `faiss_rag.py` | FAISS local index — embed with Gemini, IndexFlatIP, exact search |
| `bq_vector_search.py` | BigQuery VECTOR_SEARCH — embed, load, SQL retrieval, generate |
| `data/documents.txt` | 600-word GCP services corpus used as the RAG knowledge base |
| `requirements.txt` | Python dependencies |
| `deploy.sh` | Enable APIs, create BQ dataset |

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
