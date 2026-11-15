# 18. Text Clustering, Multimodal Embeddings & BigQuery Vectors

> Three advanced embedding patterns: unsupervised text clustering with AI-generated labels, cross-modal image+text similarity, and in-warehouse embedding with BigQuery ML. **GCP Services:** Vertex AI (Gemini Embedding API, Multimodal Embedding, Gemini), BigQuery ML

## Architecture

```
data/sample_texts.txt
        |
        v
  clustering.py ──> embed_for_clustering()  ──> CLUSTERING task type
                    cluster_texts()          ──> KMeans (n_init=10)
                    name_cluster()           ──> Gemini 2.5 Flash label generation
        |
        v
  multimodal.py ──> embed_image()            ──> multimodalembedding@001 (us-central1)
                    embed_text_multimodal()  ──> same model, shared 1408-dim space
                    cosine_similarity()      ──> cross-modal comparison
        |
        v
  bq_vectors.py ──> CREATE MODEL (remote)   ──> gemini-embedding-001 via BQ connection
                    ML.GENERATE_EMBEDDING   ──> embed rows in-warehouse
                    VECTOR_SEARCH()         ──> SQL-native nearest-neighbor (example SQL)
        |
        v
   main.py ──> runs all three parts (--skip-bq to skip BigQuery)
```

## What this demonstrates

- CLUSTERING task type: optimized for KMeans grouping, not retrieval or similarity
- KMeans on L2-normalized embedding vectors approximates spherical clustering in cosine space
- AI-generated cluster labels: Gemini names each cluster from its member texts — zero manual taxonomy
- Multimodal embeddings: `multimodalembedding@001` maps images and text into one shared 1408-dim space
- Model isolation: vectors from different models live in incompatible spaces — never mix them
- BigQuery ML: `ML.GENERATE_EMBEDDING` runs batch embedding inside the warehouse — no data egress
- `VECTOR_SEARCH()` pairs with `ML.GENERATE_EMBEDDING` for SQL-native RAG at scale

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
gcloud auth application-default login
```

BigQuery (Part 3) requires a Cloud Resource connection. Run `python main.py` — setup instructions are printed automatically if the connection is missing.

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run all three parts (BQ setup required for Part 3):
python main.py

# Skip BigQuery demo:
python main.py --skip-bq
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — orchestrates clustering, multimodal, and BQ demos |
| `clustering.py` | KMeans clustering on CLUSTERING-typed embeddings + Gemini cluster naming |
| `multimodal.py` | Image + text embedding in shared vector space via `multimodalembedding@001` |
| `bq_vectors.py` | BigQuery `ML.GENERATE_EMBEDDING` remote model setup and query |
| `data/sample_texts.txt` | 20 sample support messages for clustering demo |
| `deploy.sh` | Enable Vertex AI + BigQuery APIs and set project |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
