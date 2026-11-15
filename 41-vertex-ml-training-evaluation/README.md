# 41. Vertex AI ML Training & Evaluation

> End-to-end pipeline: Gemini batch prediction over BigQuery, supervised fine-tuning (DRY_RUN), and Gemini-as-judge evaluation scoring. **GCP Services:** Vertex AI, BigQuery, Cloud Storage, Gemini API

## Architecture

```
PROMPTS (in-memory)
        │
        ▼
┌─────────────────────┐
│  BigQuery Input     │  gemini_batch_input table
│  Table (JSONL rows) │
└────────┬────────────┘
         │  bq://
         ▼
┌─────────────────────┐
│  Gemini Batch       │  ~50% discount vs online
│  Prediction Job     │  (gemini-2.5-flash)
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  BigQuery Output    │  gemini_batch_output table
│  Table              │
└─────────────────────┘

TRAIN_EXAMPLES (in-memory)
        │
        ▼
┌─────────────────────┐
│  GCS JSONL Upload   │  gs://your-gcs-bucket/...
└────────┬────────────┘
         │  DRY_RUN=false only
         ▼
┌─────────────────────┐
│  Supervised         │  gemini-2.5-flash SFT job
│  Fine-Tuning Job    │  (BILLABLE — disabled by default)
└─────────────────────┘

EVAL DATASET (in-memory)
        │
        ▼
┌─────────────────────┐
│  Gemini-as-Judge    │  Pointwise rubric scoring
│  Evaluation         │  + Pairwise A/B comparison
└─────────────────────┘
```

## What this demonstrates

- Gemini batch prediction via BigQuery using `bq://` source/destination — 50% cost discount vs online inference
- Supervised fine-tuning (SFT) workflow: JSONL data prep to GCS, job submission via `client.tunings.tune()`
- Cost-gating pattern: `DRY_RUN=True` default prevents accidental billable fine-tuning jobs
- Gemini-as-judge evaluation: pointwise rubric scoring (groundedness + fluency) and pairwise A/B verdict
- GenAI SDK (`google-genai`) for all three Vertex AI workflows in one modular pipeline

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export GOOGLE_CLOUD_BUCKET=your-gcs-bucket   # must exist; used for fine-tuning JSONL upload
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

This enables required APIs, creates the BigQuery dataset, and configures ADC.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Fine-tuning is **DRY_RUN by default** — it uploads the training JSONL to GCS but does not submit a job. To submit a real tuning job (billable):

```bash
DRY_RUN=false python main.py
```

> **Cost warning:** Supervised fine-tuning is billed per training token x epochs. Even small jobs cost real money; large corpora can cost $100s. Always confirm before setting `DRY_RUN=false`.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator — runs batch predict → fine-tune → evaluate |
| `batch_predict.py` | Creates BQ input table, submits Gemini batch job, polls state |
| `fine_tuning.py` | Uploads JSONL to GCS, launches SFT job (DRY_RUN=True default) |
| `evaluation.py` | Gemini-as-judge pointwise + pairwise evaluation |
| `deploy.sh` | Enables APIs, creates BQ dataset, configures auth |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
