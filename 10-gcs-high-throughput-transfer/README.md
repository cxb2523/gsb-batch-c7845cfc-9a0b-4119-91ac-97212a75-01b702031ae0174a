# 10. GCS High-Throughput Transfer

> Three production-grade patterns for high-throughput GCS data movement: in-memory BytesIO streaming, parallel bulk transfer via transfer_manager, and zero-boilerplate pandas reads over gs://. **GCP Services:** Cloud Storage

## Architecture

```
Pattern A: In-Memory Streaming (no disk I/O)
──────────────────────────────────────────
DataFrame
   │ df.to_csv(BytesIO)
   ▼
BytesIO buffer ──upload_from_file()──► gs://<bucket>/demo/stream_metrics.csv
                                                   │
                              download_as_bytes() ◄┘
                                   │
                               BytesIO
                                   │ pd.read_csv()
                                   ▼
                              DataFrame (verified equal)

Pattern B: Parallel Bulk Transfer
──────────────────────────────────────────
local/src/part_00.txt ─┐
local/src/part_01.txt ─┤
local/src/part_02.txt ─┤─ upload_many_from_filenames() ──► gs://<bucket>/demo/transfer-manager/
local/src/part_03.txt ─┤   (4 workers in parallel)
local/src/part_04.txt ─┤
local/src/part_05.txt ─┘
                                                          │
                              download_many_to_path() ◄──┘
                              (4 workers in parallel)
                                    │
                              local/dst/...

Pattern C: pandas over gs://
──────────────────────────────────────────
gs://<bucket>/data/sales_data.csv
         │ pd.read_csv("gs://...")
         │ (gcsfs handles auth + I/O)
         ▼
    DataFrame → aggregations → summary output
```

## What this demonstrates

- **Pattern A**: `upload_from_file(BytesIO)` + `download_as_bytes()` for zero-disk-IO round-trips — required for Cloud Run / Cloud Functions where the filesystem is ephemeral
- **Pattern B**: `transfer_manager.upload_many_from_filenames()` and `download_many_to_path()` with configurable worker pool — for bulk ETL and batch data movement
- Per-file result list from transfer_manager: `None` = success, `Exception` = per-file failure (others continue)
- **Pattern C**: `pd.read_csv("gs://...")` via gcsfs — zero GCS client boilerplate for analytics reads
- Aggregation and summary over the downloaded DataFrame
- Timing each pattern to demonstrate throughput characteristics

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GCS_BUCKET=your-gcs-bucket    # must already exist
```

Pattern C requires the CSV already in GCS (deploy.sh handles this):
```bash
gsutil cp data/sales_data.csv gs://<your-gcs-bucket>/data/sales_data.csv
```

Authenticate:
```bash
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
| `main.py` | Orchestrator: runs all 3 patterns in sequence with summary |
| `stream.py` | Pattern A: in-memory BytesIO streaming round-trip |
| `parallel_transfer.py` | Pattern B: parallel upload/download via transfer_manager |
| `pandas_gcs.py` | Pattern C: `pd.read_csv("gs://...")` with aggregation |
| `data/sales_data.csv` | Sample data used for upload and pandas read |
| `deploy.sh` | API enablement + CSV upload to GCS |
| `requirements.txt` | Python dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
