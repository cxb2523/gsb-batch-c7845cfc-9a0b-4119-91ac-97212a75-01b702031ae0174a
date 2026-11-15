# 50. Beam Dataflow Production Submit

> Submit an Apache Beam pipeline to managed Google Cloud Dataflow. DRY_RUN=True by default — no cost until you explicitly opt in. **GCP Services:** Cloud Dataflow, Cloud Storage, BigQuery

## Architecture

```
data/sales_data.csv
        │
  gsutil cp → gs://your-gcs-bucket/data/sales_data.csv
                        │
                        ▼
              dataflow_submit.py
              PipelineOptions(runner="DataflowRunner")
                - project, region, temp_location
                - job_name (timestamped)
                - max_num_workers=2
                - machine_type=e2-standard-2
                        │
              pipeline.run()  ← returns immediately after submission
                        │
                        ▼
          ┌─────────────────────────┐
          │  Google Cloud Dataflow  │
          │  Managed service        │
          │  Autoscaling workers    │
          │  ≤ 2 × e2-standard-2   │
          └──────────┬──────────────┘
                     │  Reads from GCS
                     │  ReadFromText(gs://...)
                     │  ParseRows → KeyByRegion
                     │  CombinePerKey(sum)
                     │  WriteToText(gs://...)
                     ▼
        gs://your-gcs-bucket/output/revenue_by_region-*.txt

DRY_RUN=true (default):  prints config + cost warnings, no job submitted
DRY_RUN=false:           submits real job, bills worker VMs per second
```

## What this demonstrates

- `DataflowRunner` PipelineOptions: project, region, temp_location, job_name
- Cost controls: `max_num_workers` caps autoscaling; `machine_type` sets worker size
- `pipeline.run()` submits and returns immediately — job runs asynchronously on GCP
- GCS-only I/O: Dataflow workers have no access to local filesystems
- `DRY_RUN` gate: safe-by-default pattern for demos and CI pipelines
- Monitoring: Dataflow console URL and `gcloud dataflow jobs` CLI
- The pipeline graph is identical to project 47 — only PipelineOptions differ

## DRY_RUN flag

| Value | Behaviour | Cost |
|-------|-----------|------|
| `true` (default) | Prints config + warnings, exits without submitting | $0 |
| `false` | Submits job to Dataflow, bills worker VMs | ~cents per run |

**Always run dry first.** A forgotten streaming Dataflow job running 24/7 can
accumulate significant cost — cancel explicitly when done.

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export GCS_BUCKET=your-gcs-bucket

gcloud auth login
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

This:
1. Enables Dataflow, GCS, Compute APIs
2. Creates the GCS staging bucket
3. Uploads `data/sales_data.csv` to GCS
4. Grants the Dataflow worker SA `roles/dataflow.worker` + `roles/storage.objectAdmin`

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Dry run first (always):
GOOGLE_CLOUD_PROJECT=your-project-id \
GCS_BUCKET=your-gcs-bucket \
python main.py

# Real submission (incurs Dataflow charges):
GOOGLE_CLOUD_PROJECT=your-project-id \
GCS_BUCKET=your-gcs-bucket \
DRY_RUN=false python main.py
```

Check job status:
```bash
gcloud dataflow jobs list --project=your-project-id --region=us-central1

# Read output after job completes:
gsutil cat "gs://your-gcs-bucket/output/revenue_by_region-*.txt"
```

Cancel a running job:
```bash
gcloud dataflow jobs cancel <JOB_ID> --region=us-central1
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point: validates env, calls `run_dataflow_job()`, prints summary |
| `dataflow_submit.py` | Core logic: builds options, constructs pipeline graph, submits (or dry-runs) |
| `data/sales_data.csv` | Local fixture; deploy.sh uploads it to GCS before submission |
| `deploy.sh` | Enables APIs, creates GCS bucket, uploads data, grants SA roles |
| `requirements.txt` | Python dependencies |

## Cost breakdown

| Component | Cost model |
|-----------|-----------|
| Dataflow workers | vCPU + RAM per second × number of workers |
| GCS storage | Per GB-month (negligible for this dataset) |
| GCS operations | Per request (negligible) |
| Dataflow shuffle | Per GB shuffled (minimal for tiny dataset) |
| BigQuery (not used here) | Load jobs free; queries billed per TB scanned |

For this 20-row dataset: typically 1-2 workers × a few minutes ≈ a few cents.
Run `DirectRunner` (projects 47-49) for free local development.

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
