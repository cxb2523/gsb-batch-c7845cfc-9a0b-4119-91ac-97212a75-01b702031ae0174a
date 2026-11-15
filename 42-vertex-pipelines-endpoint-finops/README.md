# 42. Vertex AI Pipelines & Endpoint FinOps

> Audit Vertex AI endpoints for billing waste and build reproducible ML pipelines with KFP v2. **GCP Services:** Vertex AI, Vertex Pipelines, Cloud Storage

## Architecture

```
┌──────────────────────────────────┐
│  STAGE 1: Endpoint FinOps Audit  │  (READ-ONLY)
│                                  │
│  Vertex AI Endpoint.list()       │
│         │                        │
│         ▼                        │
│  For each endpoint:              │
│    Inspect traffic_split         │
│    Flag 0% traffic models  ───► Print undeploy gcloud commands
│    Flag empty endpoints    ───► Print delete gcloud commands
└──────────────────────────────────┘

┌──────────────────────────────────┐
│  STAGE 2: Minimal KFP Pipeline   │
│                                  │
│  @dsl.component: prep_data       │
│         │                        │
│         ▼                        │
│  @dsl.component: train_stub      │
│         │                        │
│         ▼                        │
│  compiler.Compiler().compile()   │──► minimal_pipeline_spec.json
│         │                        │
│  DRY_RUN=false only:             │
│         ▼                        │
│  aiplatform.PipelineJob.submit() │──► Vertex AI (billable)
└──────────────────────────────────┘
```

## What this demonstrates

- Vertex AI endpoint FinOps: enumerate all endpoints, surface 0%-traffic deployed models burning node-hours 24/7
- READ-ONLY audit pattern: always print actionable gcloud commands, never execute destructive ops programmatically
- KFP v2 pipeline definition: `@dsl.component` and `@dsl.pipeline` decorators for reproducible ML DAGs
- Pipeline compilation to JSON spec — enables versioning, CI/CD, and scheduled reuse
- Cost-gating: `DRY_RUN=True` compiles the pipeline locally without submitting billable Vertex compute

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export GOOGLE_CLOUD_BUCKET=your-gcs-bucket   # used as pipeline root for real runs
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

To submit a real pipeline job (billable compute per step, ~$0.03/run fee):

```bash
DRY_RUN=false python main.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator — runs endpoint audit then pipeline |
| `endpoint_finops.py` | Lists all endpoints, flags 0%-traffic models, prints remediation commands |
| `pipelines.py` | KFP v2 pipeline definition, compilation, optional Vertex submission |
| `deploy.sh` | Enables APIs and configures auth |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
