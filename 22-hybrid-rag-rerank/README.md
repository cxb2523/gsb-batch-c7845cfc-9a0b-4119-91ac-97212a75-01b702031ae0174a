# 22. Hybrid RAG with BM25 + Dense Vector Search + Reranking

> Production-grade retrieval pipeline combining BM25 keyword search, Gemini dense embeddings, Reciprocal Rank Fusion, and Gemini cross-encoder reranking. **GCP Services:** Vertex AI (Gemini Embeddings, Gemini Flash)

## Architecture

```
Query
  |
  +--------> [BM25 / rank_bm25]  ---------> BM25 ranking (exact token match)
  |                                                 |
  +--------> [Gemini Embeddings]  --------> Vector ranking (semantic similarity)
                                                    |
                                    [Reciprocal Rank Fusion (RRF)]
                                            |
                                    Fused top-K candidates
                                            |
                              [Gemini 2.5 Flash — Cross-Encoder Reranker]
                                     (rates relevance 0-10 per candidate)
                                            |
                                    Final ranked results
                                            |
                              [Gemini] Grounded Answer
```

## What this demonstrates

- BM25 keyword search: exact-token ranking via rank_bm25; wins on rare tokens, error codes, schema names, and proper nouns
- Dense vector search: Gemini Embeddings API with cosine similarity; wins on paraphrase and semantic synonyms
- Reciprocal Rank Fusion (RRF): rank-based fusion with no score calibration — works reliably across retrievers with different score scales
- Gemini as cross-encoder reranker: joint query+document attention over the small fused candidate set; highest accuracy at negligible cost when applied to top-k only
- Side-by-side comparison of BM25, vector, and hybrid rankings to illustrate when each retriever wins

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
```

Authenticate:
```bash
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
| `main.py` | Orchestrates full hybrid search pipeline; runs 3 demo queries |
| `keyword_search.py` | BM25 index build and ranking via rank_bm25 |
| `vector_search.py` | Gemini Embeddings + cosine similarity ranking |
| `reranker.py` | RRF fusion + Gemini cross-encoder reranker |
| `data/documents.txt` | 8 clearly distinct GCP topic sections for high-contrast demo |
| `requirements.txt` | Python dependencies |
| `deploy.sh` | Enable required GCP APIs |

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
