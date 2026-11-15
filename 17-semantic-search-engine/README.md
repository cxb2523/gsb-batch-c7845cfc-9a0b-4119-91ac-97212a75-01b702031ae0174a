# 17. Semantic Search Engine

> Build a pure-numpy semantic search engine over a product catalog using Vertex AI embeddings — no vector database required. **GCP Services:** Vertex AI (Gemini Embedding API)

## Architecture

```
product_catalog.csv
        |
        v
   embedder.py ──> embed_documents()   ──> RETRIEVAL_DOCUMENT task type
                   embed_query()       ──> RETRIEVAL_QUERY task type
        |
        v
   search.py ──> CosineSimilarityIndex  ──> matmul over L2-normalized matrix
                 ProductCatalogSearch   ──> catalog with metadata (id, category, price)
                 print_results()        ──> ranked output with scores
        |
        v
   main.py ──> Part 1: raw cosine similarity over GCP service corpus
               Part 2: natural-language queries over product catalog CSV
```

## What this demonstrates

- Asymmetric task types: `RETRIEVAL_DOCUMENT` for corpus, `RETRIEVAL_QUERY` for queries — why this matters for recall
- Brute-force cosine similarity as a numpy matmul (exact, sub-millisecond for corpora under 100K vectors)
- Catalog search: embed name + description + category as a single rich string for better signal
- Deduplication before embedding: one API call per unique product, not per sale row
- Metadata passthrough: price, category, product id attached to ranked results
- Semantic matching: "cost control for BigQuery" → BigQuery Flat-Rate Reservation (no keyword overlap needed)

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — runs cosine search demo and catalog search demo |
| `embedder.py` | Embedding generation with correct task types, normalized output |
| `search.py` | `CosineSimilarityIndex` (pure numpy) + `ProductCatalogSearch` (CSV catalog) |
| `data/product_catalog.csv` | 20-row GCP product catalog with id, name, description, category, price |
| `deploy.sh` | Enable Vertex AI API and set project |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
