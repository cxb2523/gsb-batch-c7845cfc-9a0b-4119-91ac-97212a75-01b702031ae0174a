# 53. Composer GCS Sensor Trigger

> Event-driven pipeline triggered by a file landing in GCS. Sensor → process → archive → Pub/Sub notify. **GCP Services:** Cloud Composer 2, Cloud Storage, Pub/Sub, Cloud Logging.

## Architecture

```
External System (POS, ETL service, API gateway)
    │
    │  DROP file at:
    │  gs://your-gcs-bucket/landing/events/sales_YYYYMMDD_HHMMSS.json
    ▼
GCS Landing Bucket
    │
    ▼
GCSObjectsWithPrefixExistenceSensor
(mode=reschedule, polls every 60s, 14-min timeout per cycle)
    │  fires when ≥1 object matches prefix
    ▼
PythonOperator: register_detected_files
(reads sensor XCom, logs file list)
    │
    ▼
PythonOperator: process_landing_files
(GCSHook.download → parse JSON/CSV → validate → XCom results)
    │
    ▼
PythonOperator: archive_processed_files
(GCSHook.copy landing→archive, GCSHook.delete from landing)
    │
    ▼
GCS Archive Bucket
(gs://your-gcs-bucket/archive/processed/YYYY-MM-DD/<filename>)
    │
    ▼
PubSubPublishMessageOperator
(publishes JSON completion event to your-pubsub-topic)
    │
    ▼
Pub/Sub Topic: your-pubsub-topic
(downstream consumers: BigQuery subscription, Cloud Function, Dataflow)
```

## What this demonstrates

- `GCSObjectsWithPrefixExistenceSensor` with `mode="reschedule"` (does not hold worker slot between pokes — critical for long-polling at scale)
- `GCSHook` operations: `list()`, `download()`, `get_metadata()`, `copy()`, `delete()`, `upload()`
- GCS move pattern: copy-then-delete (GCS has no native rename/move)
- `PubSubPublishMessageOperator` for publishing structured completion events
- XCom chaining across multiple PythonOperator tasks
- `max_active_runs=1` to prevent concurrent sensor runs racing on the same files
- Event-driven schedule (`*/15 * * * *`) vs the traditional daily batch

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export COMPOSER_ENV=your-composer-env
export COMPOSER_BUCKET=your-composer-bucket   # gs:// bucket auto-created by Composer
export LANDING_BUCKET=your-gcs-bucket
export PUBSUB_TOPIC=your-pubsub-topic
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

The script:
1. Enables Composer, Storage, and Pub/Sub APIs
2. Creates GCS bucket with `landing/events/`, `archive/processed/`, and `processed/` prefixes
3. Creates Pub/Sub topic and pull subscription for testing
4. Uploads `data/sample_payload.json` to the landing prefix to pre-seed a trigger
5. Creates Composer 2 environment (`--async`)

## Deploy DAGs

```bash
export COMPOSER_BUCKET=$(
  gcloud composer environments describe "$COMPOSER_ENV" \
    --location "$GOOGLE_CLOUD_LOCATION" \
    --format='value(config.dagGcsPrefix)'
)

gcloud storage cp dags/*.py "$COMPOSER_BUCKET/"
```

## Airflow Variables

```bash
for KV in \
  "project_id=your-project-id" \
  "landing_bucket=your-gcs-bucket" \
  "archive_bucket=your-gcs-bucket" \
  "output_bucket=your-gcs-bucket" \
  "pubsub_topic=your-pubsub-topic" \
  "gcs_watch_prefix=landing/events/"; do
  KEY="${KV%%=*}"; VAL="${KV##*=}"
  gcloud composer environments run "$COMPOSER_ENV" \
    --location "$GOOGLE_CLOUD_LOCATION" \
    variables -- set "$KEY" "$VAL"
done
```

## Run

```bash
# Manually trigger a pipeline run
gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  dags trigger -- gcs_sensor_dag

# Drop a new file to trigger the next scheduled cycle
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
gcloud storage cp data/sample_payload.json \
  "gs://your-gcs-bucket/landing/events/sales_${TIMESTAMP}.json"

# Monitor Pub/Sub completions
gcloud pubsub subscriptions pull your-pubsub-topic-sub --auto-ack --limit=5
```

## GCS Folder Structure

```
gs://your-gcs-bucket/
├── landing/
│   └── events/                   ← drop files here to trigger pipeline
│       └── sales_YYYYMMDD_HHMMSS.json
├── archive/
│   └── processed/
│       └── YYYY-MM-DD/           ← archived after successful processing
│           └── sales_*.json
└── processed/
    └── YYYY-MM-DD/               ← transformed output (gcs_file_processor DAG)
        └── sales_*.processed.json
```

## Files

| File | Purpose |
|---|---|
| `dags/gcs_sensor_dag.py` | Main event-driven pipeline: sensor → process → archive → Pub/Sub |
| `dags/gcs_file_processor.py` | Standalone GCSHook CRUD reference and file processing demo |
| `data/sample_payload.json` | Sample JSON payload to drop in the landing prefix |
| `deploy.sh` | GCS bucket + Pub/Sub topic + Composer env setup |
| `requirements.txt` | Airflow provider dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
