# 14. Gemini Enterprise Features

> Three enterprise-grade Gemini capabilities: Google Search grounding for live-web-backed answers, context caching for cost-efficient repeated queries over large documents, and batch prediction for high-throughput offline inference at 50% discount. **GCP Services:** Vertex AI (Gemini API), Cloud Storage

## Architecture

```
Google Search (live web)
    │  grounding_metadata (queries + sources)
    ▼
genai.Client → grounded generate_content()
                                            ← live, cited answers

GCS PDF (large document)
    │
    ▼
client.caches.create()  ─── cache (billed once at full price)
    │
    ▼
generate_content(cached_content=cache.name)
    │                                       ← incremental prompt tokens only
    ▼
client.caches.delete()  ─── stop storage billing

Batch JSONL (GCS input)
    │
    ▼
client.batches.create()  ─── async job (50% cost discount)
    │
    ▼
GCS output JSONL         ─── results when job completes (minutes–hours)
```

## What this demonstrates

- Google Search grounding: model issues live searches, answers include citations — inspect `grounding_metadata` for queries and source URLs
- Context caching: `client.caches.create()` → `cached_content=cache.name` in config — `cached_content_token_count` shows the discounted portion of input tokens
- Minimum cache token threshold: 1,024 tokens for 2.5 Flash, 2,048 for 2.5 Pro — too-small content will fail to cache
- Cache TTL and explicit deletion — cache storage is billed per token-hour
- Batch prediction: JSONL in → JSONL out, async, 50% cheaper than online inference, no QPM pressure

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export GCS_BUCKET=your-gcs-bucket        # for batch prediction only
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

Batch prediction is skipped if `GCS_BUCKET` is not set or is the placeholder value.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrates grounding → caching → batch prediction |
| `grounding.py` | Google Search grounding with source citation extraction |
| `caching.py` | Context cache creation, querying, and cleanup |
| `batch_predict.py` | JSONL input creation, job submission, and lifecycle polling |
| `deploy.sh` | Enable Vertex AI + Storage APIs and set project |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
