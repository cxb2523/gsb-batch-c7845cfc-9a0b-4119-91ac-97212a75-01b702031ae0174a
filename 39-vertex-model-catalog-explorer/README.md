# 39. Vertex AI Model Catalog Explorer

> Lists all publisher/foundation models available to your project via the unified Gen AI SDK, filters by capability category, and prints a formatted catalog with version-pinning guidance. **GCP Services:** Vertex AI (Model Garden)

## Architecture

```
main.py
  |
  |-- model_catalog.py -----> Vertex AI / Gen AI SDK
        genai.Client.models.list()
          |
          |-- list_publisher_models()  -> Full model list with actions
          |-- filter_models_by_capability()  -> Filter by keyword
          |
          v
        Full Catalog Table
        Capability Breakdown (gemini / embed / imagen / code / vision)
        Production Version Pinning Tip
```

## What this demonstrates

- Using the unified `google-genai` SDK with `vertexai=True` to list publisher models
- Extracting supported actions / generation methods from model metadata
- Filtering the model catalog by capability keywords (gemini, embed, imagen, code, vision)
- Counting models per capability bucket for quick discovery
- Best-practice guidance: pin exact model version strings in production (avoid floating aliases)
- Zero-cost read-only operation — safe to run on any schedule

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
| List publisher models | `roles/aiplatform.user` |

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
|---|---|
| `main.py` | Fetches catalog, prints full table, capability breakdown, and version tip |
| `model_catalog.py` | Publisher model listing and capability filtering via Gen AI SDK |
| `deploy.sh` | Enables Vertex AI API and prints IAM grant commands |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
