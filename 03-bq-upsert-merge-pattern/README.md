# 3. BigQuery Upsert / MERGE Pattern

> The canonical warehouse incremental-load pattern: partitioned table creation, atomic MERGE upsert from staging, and point-in-time snapshot for pre-DML backups. **GCP Services:** BigQuery, Cloud Storage

## Architecture

```
GCS CSV ──► Flat Table ──► CTAS DDL ──► Partitioned + Clustered Table
                                              │
                                              ▼
Batch Data ──► Staging Table ──► MERGE (atomic upsert) ──► Target Table
                                              │
Source Table ──► Copy Job  ──► Copy Table
             └──► Snapshot Job ──► Table Snapshot (read-only, delta-billed)
```

## What this demonstrates

- Loading CSV from GCS into a flat (unpartitioned) table as a baseline
- Creating a `PARTITION BY order_date CLUSTER BY region, category` table via `CREATE OR REPLACE TABLE ... AS SELECT`
- Dry-run bytes comparison to quantify partition pruning savings vs a full-table scan
- The staging → `MERGE` → target incremental pattern: atomic, idempotent, one-commit upsert
- `WHEN MATCHED AND newer_date` guard to skip no-op updates (avoids unnecessary DML-touched bytes)
- `WHEN NOT MATCHED THEN INSERT` for net-new rows in the same atomic statement
- Copy jobs (free, no slots) for table clones
- Point-in-time table `SNAPSHOT` as the lightweight "backup before risky DML" strategy

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id   # ← replace
export GOOGLE_CLOUD_LOCATION=us-central1
export GCS_BUCKET=your-gcs-bucket             # ← replace
export BQ_DATASET=your_dataset                # ← replace
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
| `main.py` | Entry point — orchestrates all five demo steps |
| `schema.py` | Flat/partitioned table creation, bytes comparison, copy job, snapshot |
| `merge.py` | `run_upsert_pipeline()` — seed target, load staging, MERGE, show result |
| `data/sales_data.csv` | Sample sales fixture (20 rows, GCP product orders) |
| `deploy.sh` | Enables APIs, creates dataset, bucket, uploads CSV |
| `requirements.txt` | Python dependencies |

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
