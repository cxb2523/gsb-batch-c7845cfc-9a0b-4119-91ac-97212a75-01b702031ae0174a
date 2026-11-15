# 33. Dataproc Serverless PySpark

> Stage a PySpark script to GCS and submit it as a Dataproc Serverless batch — no cluster to create, manage, or tear down. **GCP Services:** Dataproc Serverless, Cloud Storage

## Architecture

```
main.py
   │
   ├─[Step 1]─► spark_job.py: stage_pyspark_script()
   │                  │
   │             Upload PYSPARK_SCRIPT (inline)
   │                  │
   │             gs://your-gcs-bucket/dataproc-staging/wordcount_job.py
   │
   └─[Step 2]─► spark_job.py: submit_batch()
                    │  (google-cloud-dataproc BatchControllerClient)
                    ▼
            Dataproc Serverless API  ──► Serverless Batch (async)
                                              │
                                    Managed Spark Workers
                                    (auto-provisioned, ~60s spin-up)
                                              │
                                              ▼
                                  gs://your-gcs-bucket/dataproc-output/
                                  wordcount_output/ (CSV results)
```

## What this demonstrates

- Two-phase Serverless submit pattern: stage script to GCS, then call BatchControllerClient
- Submit-and-exit (async): `create_batch` returns an LRO immediately; do not block on `result()`
- Cost-safety: `DRY_RUN=true` by default; real batch only on explicit `DRY_RUN=false`
- Serverless vs cluster trade-off: zero idle cost, per-job isolation, automatic autoscaling
- Permission error handling: Dataproc API not enabled, missing `roles/dataproc.editor`
- PySpark word-count pattern: read text → explode → groupBy → orderBy → write CSV

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

The script enables `dataproc.googleapis.com` and `storage.googleapis.com`, creates the GCS staging bucket, and grants `roles/dataproc.worker` to the default compute service account.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

**By default `DRY_RUN=true`** — the PySpark script is staged to GCS (tiny file, negligible cost) but no Dataproc batch is submitted.

To submit a real paid Dataproc Serverless batch:

```bash
export DRY_RUN=false
python main.py
```

> **Cost warning:** Dataproc Serverless bills per DCU-hour with a ~2 DCU / 1-minute minimum per batch. Every submission spins real Spark infrastructure — do not loop submissions. Monitor at the [Dataproc console](https://console.cloud.google.com/dataproc/batches).

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — stages script, invokes batch submitter, shows result |
| `spark_job.py` | GCS upload + Dataproc Serverless batch submission logic |
| `deploy.sh` | Enables APIs, creates GCS bucket, grants worker IAM role |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
