# 30. BigQuery ETL Pipeline

> Full batch ETL: extract CSV, transform with pandas, stage as Parquet on GCS, load to BigQuery (free batch job), verify row counts, and run an incremental MERGE upsert — the production-grade pattern that scales to any data volume. **GCP Services:** BigQuery, Cloud Storage, BigQuery Storage Read API

## Architecture

```
data/sales_data.csv
        │
        ├── Step 1: Extract
        │          └── pandas.read_csv (typed) + BQ roundtrip demo
        │
        ├── Step 2: Transform (pandas)
        │          ├── Deduplicate on order_id
        │          ├── order_date datetime64 -> Python date (BQ DATE)
        │          ├── Normalize region (UNKNOWN fill, uppercase)
        │          └── Recompute missing total_amount
        │
        ├── Step 3: Stage
        │          └── Parquet (snappy) -> GCS bucket (replayable artifact)
        │
        ├── Step 4: Load (FREE batch job)
        │          └── BQ load_table_from_uri (PARQUET) -> sales_etl
        │
        ├── Step 4a: Verify
        │          └── COUNT(*) assertion (never trust a pipeline that doesn't count)
        │
        └── Step 5: Incremental MERGE
                   ├── Seed target with 80% history
                   ├── Stage delta + correction in staging table
                   ├── MERGE INTO target ON order_id (upsert)
                   └── Idempotency proof: replay same batch, counts unchanged
```

## What this demonstrates

- `load_table_from_dataframe` serializes via Parquet — free batch load, no streaming insert cost
- GCS staging as a durable, replayable ETL artifact (decoupled from the source)
- `load_table_from_uri` with `SourceFormat.PARQUET` — schema comes from the file, no config needed
- `WRITE_TRUNCATE` for idempotent full loads; `WRITE_APPEND + MERGE` for incremental
- MERGE pattern: atomic upsert keyed on business key — handles late data corrections
- MERGE idempotency proof: replaying a batch is a no-op insert-wise
- BigQuery Storage Read API via `to_dataframe()` — Arrow stream, faster than paging REST API
- `db-dtypes` requirement for BQ -> pandas type conversion (DATE, NUMERIC, etc.)

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GCS_BUCKET=your-gcs-bucket      # created by deploy.sh if omitted

# Authenticate
gcloud auth application-default login
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

`deploy.sh` enables BigQuery, GCS, and BigQuery Storage APIs, and creates the GCS bucket.

## Run

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GCS_BUCKET=your-gcs-bucket
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — orchestrates all ETL stages |
| `extract.py` | CSV loading and pandas <-> BigQuery roundtrip |
| `transform.py` | Pandas cleaning, date conversion, derived columns |
| `load.py` | GCS staging, BQ load job, row-count verification, incremental MERGE |
| `data/sales_data.csv` | Sample sales dataset (20 rows) |
| `requirements.txt` | Python dependencies |
| `deploy.sh` | Enables APIs and creates GCS bucket |

## Cost

| Operation | Cost |
|-----------|------|
| BQ load jobs | **FREE** |
| BQ queries (COUNT, MERGE) | Fractions of a cent (10 MB min billing) |
| GCS storage | Negligible (< 1 MB Parquet) |
| BigQuery Storage Read API | Negligible for 20 rows |

## MERGE vs Truncate-Reload

| Factor | Truncate-Reload | MERGE |
|--------|----------------|-------|
| At-scale cost | Rewrites entire table | Touches only affected partitions |
| Late data corrections | Re-extract full history | Updates in place |
| Idempotency | Risky (duplication risk) | Safe (keyed upsert) |
| Complexity | Simple | Slightly more setup |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
