# 16. Text Embeddings Fundamentals

> Generate, inspect, and compare Vertex AI text embeddings using gemini-embedding-001 — from a single vector to batch task-typed embeddings and dimensionality trade-offs. **GCP Services:** Vertex AI (Gemini Embedding API)

## Architecture

```
sample_texts.txt
      |
      v
 embeddings.py ──> embed_single()          ──> vector inspection (length, norm, values)
                   embed_batch_documents()  ──> RETRIEVAL_DOCUMENT task type
                   embed_query()            ──> RETRIEVAL_QUERY task type
                   rank_documents()         ──> cosine similarity ranking
      |
      v
dimensionality.py ─> embed_pair_at_dim()   ──> 3072 / 768 / 256 dims
                     cosine_similarity()    ──> normalized dot product
                                            ──> storage cost comparison
      |
      v
   main.py ─────> runs all patterns in sequence
```

## What this demonstrates

- Single embedding with vector inspection (length, L2 norm, value distribution)
- Why task types (`RETRIEVAL_DOCUMENT` vs `RETRIEVAL_QUERY`) improve retrieval recall
- Batch embedding: one API call for many documents — cheaper and faster than a loop
- Matryoshka Representation Learning (MRL): truncating to 768 or 256 dims drops storage ~4x/12x with minimal cosine similarity loss
- How to normalize truncated vectors before cosine search
- Storage math: 3072 dims = ~12 GB/million vectors vs 256 dims = ~1 GB/million

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
| `main.py` | Entry point — runs all three embedding pattern demos |
| `embeddings.py` | Single embed, batch embed with task types, cosine ranking |
| `dimensionality.py` | Output dimensionality comparison (3072 / 768 / 256) |
| `data/sample_texts.txt` | 20 sample product/tech descriptions for demo |
| `deploy.sh` | Enable Vertex AI API and set project |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
