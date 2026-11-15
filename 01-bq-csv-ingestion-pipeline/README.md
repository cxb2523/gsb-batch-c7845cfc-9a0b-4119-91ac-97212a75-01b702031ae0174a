# 1. BigQuery CSV Ingestion Pipeline

> Three ingestion modes in one project: local file upload, GCS CSV load with explicit schema, and JSONL/Parquet format loading. **GCP Services:** BigQuery, Cloud Storage

## Architecture

```
Local CSV File ──────────────────────────────► load_table_from_file ──► BQ Table (sales_data)
                                                     │
GCS CSV File  ──► gs://bucket/data/sales_data.csv ──► load_table_from_uri ──► BQ Table (sales_data_gcs)
                                                     │
pandas DataFrame ──► JSONL/Parquet ──► GCS ──────────► load_table_from_uri ──► BQ Tables (json/parquet)
```

## What this demonstrates

- Loading a local CSV into BigQuery using `load_table_from_file` with schema autodetect
- Loading CSV from Cloud Storage with an explicit `SchemaField` schema (production-safe)
- Writing JSONL and Parquet fixtures to GCS and ingesting them with the matching `source_format`
- `WRITE_TRUNCATE` write disposition for idempotent pipeline runs
- Difference between autodetect (dev/one-off) and explicit schema (production)

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id   # ← replace
export GOOGLE_CLOUD_LOCATION=us-central1
export GCS_BUCKET=your-gcs-bucket             # ← replace
export BQ_DATASET=your_dataset                # ← replace (default: your_dataset)
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

python main.py                  # runs all three ingestion modes
python main.py --mode local     # local CSV only
python main.py --mode gcs       # GCS CSV with explicit schema
python main.py --mode formats   # JSONL + Parquet from GCS
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — accepts `--mode local\|gcs\|formats`, runs all if omitted |
| `ingest_local.py` | `load_csv_local()` — streams local CSV bytes into a BigQuery load job |
| `ingest_gcs.py` | `load_csv_from_gcs()` — server-side GCS load with explicit schema |
| `ingest_formats.py` | `load_json_and_parquet()` — uploads JSONL + Parquet to GCS, loads both into BQ |
| `data/sales_data.csv` | Sample sales fixture (20 rows, GCP product orders) |
| `deploy.sh` | Creates dataset, bucket, uploads CSV |
| `requirements.txt` | Python dependencies |

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
