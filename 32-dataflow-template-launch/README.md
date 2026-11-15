# 32. Dataflow Template Launch

> Submit a Google-provided Dataflow classic template (Word_Count) via the Dataflow REST API. Safe dry-run mode by default. **GCP Services:** Cloud Dataflow, Cloud Storage

## Architecture

```
main.py
   │
   ▼
dataflow_launcher.py
   │  (google-api-python-client)
   ▼
Dataflow REST API  ──► Dataflow Job (async)
                            │
                    Worker VMs (n1-standard-1, max 2)
                            │
                            ▼
              gs://your-gcs-bucket/dataflow-output/
              (Word_Count results from Shakespeare corpus)
```

## What this demonstrates

- Launching a Google-provided classic Dataflow template via `googleapiclient`
- Async job submission pattern: submit-and-exit, monitor separately
- Cost-safety pattern: `DRY_RUN=true` by default, real job only on explicit opt-in
- `maxWorkers` cap as the primary Dataflow cost-control mechanism
- Graceful HTTP error handling (403 permission, 429 quota, API-not-enabled)
- Template parameter and environment configuration structure

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export GCS_BUCKET=your-gcs-bucket
```

Authenticate:

```bash
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

The script enables `dataflow.googleapis.com` and `storage.googleapis.com`, and creates the GCS staging bucket.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

**By default `DRY_RUN=true`** — the job parameters are printed but no API call is made and no charges are incurred.

To submit a real paid Dataflow job:

```bash
export DRY_RUN=false
python main.py
```

> **Cost warning:** A real Dataflow job spins up worker VMs (n1-standard-1) billed per second while the job runs. Word_Count on the sample Shakespeare corpus costs a few cents. Always check the [Dataflow console](https://console.cloud.google.com/dataflow) to confirm the job completes and stops.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — prints config, invokes launcher, shows result |
| `dataflow_launcher.py` | Template launch logic: builds request body, calls REST API |
| `deploy.sh` | Enables APIs, creates GCS staging bucket |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
