# 48. Beam CSV to BigQuery Load

> Load a CSV file into BigQuery using Apache Beam WriteToBigQuery — runs on DirectRunner locally but performs a real BigQuery load job. **GCP Services:** BigQuery, Cloud Storage, (optional) Cloud Dataflow

## Architecture

```
data/sales_data.csv
        │
        ▼
  pipeline.py
  ReadFromText (skip header)
        │
  Map (parse_csv_line)         ← schema.py defines columns + types
        │
  WriteToBigQuery
    method: FILE_LOADS
    create: CREATE_IF_NEEDED   ← creates table from schema string
    write:  WRITE_TRUNCATE     ← idempotent: replaces table on each run
        │
        ├── Stage JSON → GCS (gs://your-gcs-bucket/beam-tmp/)
        │
        └── BQ Load Job → your_dataset.beam_sales
                              │
                         BigQuery Table
```

## What this demonstrates

- `WriteToBigQuery` FILE_LOADS method: stages to GCS then triggers a load job
- Schema as code: string schema (`name:TYPE,...`) from a central `schema.py`
- `CREATE_IF_NEEDED` + `WRITE_TRUNCATE` for idempotent pipeline runs
- `custom_gcs_temp_location`: required for DirectRunner to stage temp files
- Real BQ load job fires even on DirectRunner — not just a local simulation
- When to use `bq load` vs Beam: no transforms → `bq load`; per-row logic → Beam

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export GCS_BUCKET=your-gcs-bucket   # or leave unset; deploy.sh sets a default

gcloud auth login
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

This creates:
- The BigQuery dataset `your_dataset`
- A GCS staging bucket for WriteToBigQuery temp files

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

GOOGLE_CLOUD_PROJECT=your-project-id \
GCS_BUCKET=your-gcs-bucket \
python main.py
```

Verify the load:
```bash
bq head -n 5 your-project-id:your_dataset.beam_sales
bq query --use_legacy_sql=false \
  'SELECT region, SUM(total_amount) AS revenue FROM your_dataset.beam_sales GROUP BY 1 ORDER BY 2 DESC'
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point: validates env vars, prints schema, calls `run_pipeline()` |
| `pipeline.py` | Beam pipeline: ReadFromText → parse → WriteToBigQuery |
| `schema.py` | Single source of truth for CSV columns, BQ schema string, and field docs |
| `data/sales_data.csv` | 20-row GCP services sales fixture |
| `deploy.sh` | Enables APIs, creates GCS bucket and BQ dataset |
| `requirements.txt` | Python dependencies |

## Cost notes

- BigQuery **load jobs are free** — only storage and query scans are billed
- GCS temp staging: tiny JSON files, negligible cost (delete `beam-tmp/` after runs)
- DirectRunner: runs on your local machine, no Dataflow worker VMs billed

## Dataflow upgrade

To run on managed Dataflow, change `pipeline.py` options:
```python
options = PipelineOptions(
    runner="DataflowRunner",
    project="your-project-id",
    region="us-central1",
    temp_location="gs://your-gcs-bucket/tmp/",
)
```
And point `csv_path` at a `gs://` URI (local paths don't exist on Dataflow workers).

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
