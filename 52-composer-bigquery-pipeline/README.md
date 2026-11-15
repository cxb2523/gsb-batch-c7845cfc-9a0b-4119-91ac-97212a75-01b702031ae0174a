# 52. Composer BigQuery Pipeline

> Full orchestrated pipeline: GCS file detection → BigQuery load → SQL transform → validation → GCS export. **GCP Services:** Cloud Composer 2, BigQuery, Cloud Storage, Cloud Logging.

## Architecture

```
GCS Landing Bucket
(gs://your-gcs-bucket/landing/sales/YYYY-MM-DD/sales_data.csv)
          │
          ▼
GCSObjectExistenceSensor
(polls every 60s until file appears)
          │
          ▼
BigQueryInsertJobOperator  ← LOAD job
(GCS CSV → sales_raw_YYYYMMDD)
          │
          ▼
BigQueryInsertJobOperator  ← DML MERGE (staging_load.sql)
(sales_raw_YYYYMMDD → sales_staging, idempotent MERGE)
          │
          ▼
BigQueryInsertJobOperator  ← DML INSERT (transform.sql)
(sales_staging → sales_summary, aggregated by region + category)
          │
          ▼
BigQueryCheckOperator  ← validate.sql
(row count + data quality checks; fails task if checks return 0)
          │
          ▼
BigQueryInsertJobOperator  ← EXTRACT job
(sales_summary → GCS results/sales_summary/YYYY-MM-DD/*.csv)
          │
          ▼
GCS Results Bucket
(gs://your-gcs-bucket/results/sales_summary/YYYY-MM-DD/)
```

## What this demonstrates

- `GCSObjectExistenceSensor` with `mode="reschedule"` (frees worker slot between pokes)
- `BigQueryInsertJobOperator` for three job types: LOAD, DML query, and EXTRACT
- `BigQueryCheckOperator` using an external SQL file with multi-check logic
- `BigQueryValueCheckOperator` and `BigQueryIntervalCheckOperator` (in `bq_operators_demo.py`)
- `BigQueryGetDataOperator` pulling rows into XCom
- `BigQueryCreateEmptyDatasetOperator` and `BigQueryCreateEmptyTableOperator` for idempotent setup
- External SQL files with Jinja `{{ ds }}`, `{{ ds_nodash }}`, `{{ params.* }}` templating
- MERGE pattern for idempotent staging loads (re-run safe)
- DATE-partitioned BigQuery tables

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export COMPOSER_ENV=your-composer-env
export COMPOSER_BUCKET=your-composer-bucket   # gs:// bucket auto-created by Composer
export GCS_BUCKET=your-gcs-bucket
export BQ_DATASET=your_dataset
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

## Deploy DAGs

```bash
# Get Composer GCS bucket
export COMPOSER_BUCKET=$(
  gcloud composer environments describe "$COMPOSER_ENV" \
    --location "$GOOGLE_CLOUD_LOCATION" \
    --format='value(config.dagGcsPrefix)'
)

# Upload DAGs and SQL files
gcloud storage cp dags/*.py "$COMPOSER_BUCKET/"

# SQL files must be accessible from the Composer worker
# Option A: place sql/ alongside dags/ in the Composer bucket
gcloud storage cp sql/*.sql "${COMPOSER_BUCKET%/dags}/sql/"

# Upload today's sales data to trigger the pipeline
TODAY=$(date +%Y-%m-%d)
gcloud storage cp data/sales_data.csv \
  "gs://your-gcs-bucket/landing/sales/$TODAY/sales_data.csv"
```

## Airflow Variables

```bash
gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  variables -- set project_id your-project-id

gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  variables -- set gcs_bucket your-gcs-bucket

gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  variables -- set bq_dataset your_dataset

gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  variables -- set bq_location US
```

## Run

```bash
# Trigger the full pipeline
gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  dags trigger -- bq_pipeline_dag

# Run the operator reference demo
gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  dags trigger -- bq_operators_demo
```

## BigQuery Tables Created

| Table | Description |
|---|---|
| `your_dataset.sales_raw_YYYYMMDD` | Raw load from CSV (one table per date, auto-created) |
| `your_dataset.sales_staging` | MERGE target — DATE-partitioned, deduplicated |
| `your_dataset.sales_summary` | Final aggregated output — DATE-partitioned by `report_date` |

## Files

| File | Purpose |
|---|---|
| `dags/bq_pipeline_dag.py` | Main 6-step orchestrated pipeline |
| `dags/bq_operators_demo.py` | Standalone reference for all BQ operators |
| `sql/staging_load.sql` | Idempotent MERGE: raw → staging |
| `sql/transform.sql` | Aggregation: staging → sales_summary |
| `sql/validate.sql` | Multi-check validation query for BigQueryCheckOperator |
| `data/sales_data.csv` | 20-row sample CSV to seed the pipeline |
| `deploy.sh` | GCS bucket + BQ dataset + Composer env setup |
| `requirements.txt` | Airflow provider dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
