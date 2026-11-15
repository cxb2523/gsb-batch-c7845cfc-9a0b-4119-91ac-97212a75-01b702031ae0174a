# 54. Composer Dataflow Orchestration

> Airflow DAG orchestrating a Dataflow Flex Template job: launch → monitor → BigQuery validate → Pub/Sub notify. Includes DRY_RUN pattern. **GCP Services:** Cloud Composer 2, Dataflow, BigQuery, Artifact Registry, Cloud Storage, Pub/Sub.

## Architecture

```
Airflow Scheduler (Cloud Composer 2)
          │
          ▼
preflight_check (PythonOperator)
  — validates Airflow Variables, logs full config
  — DRY_RUN mode: exits here without launching job
          │
          ▼
BranchPythonOperator: branch_dry_run_or_launch
    ├── [DRY_RUN=true]  → dry_run_complete → end
    └── [DRY_RUN=false] → DataflowStartFlexTemplateOperator
                               │
                               │  Flex Template spec: gs://your-gcs-bucket/dataflow/templates/
                               │  Container image:    us-central1-docker.pkg.dev/your-project-id/...
                               ▼
                         GCP Dataflow (Flex Template job)
                         workers: n1-standard-2, autoscale 1–10
                               │
                               │  reads:  gs://your-gcs-bucket/landing/sales/YYYY-MM-DD/*.json
                               │  writes: your-project-id:your_dataset.sales_dataflow_output
                               ▼
                         DataflowJobStatusSensor
                         (mode=reschedule, polls every 60s, 4hr timeout)
                               │
                               ▼
                         BranchPythonOperator: evaluate_dataflow_outcome
                             ├── [FAILED]  handle_dataflow_failure → end
                             └── [SUCCESS] BigQueryInsertJobOperator (run BQ stats query)
                                               │
                                               ▼
                                         BigQueryCheckOperator (assert rows > 0)
                                               │
                                               ▼
                                         PubSubPublishMessageOperator
                                               │
                                               ▼
                                         log_final_pipeline_summary → end
```

## What this demonstrates

- `DataflowStartFlexTemplateOperator` — full parameter set: `containerSpecGcsPath`, `parameters`, `environment` (workers, SA, network, labels, experiments)
- `do_xcom_push=True` — Dataflow job ID flows from launcher → sensor → downstream tasks
- `DataflowJobStatusSensor` with `mode="reschedule"` — 4-hour polling window without tying up a worker slot
- `DataflowJobAutoScalingEventsSensor` — observe worker autoscaling events mid-job
- `DataflowJobMessagesSensor` — stream Dataflow job log messages into Airflow logs
- `DataflowTemplatedJobStartOperator` — Classic Template syntax (migration reference)
- DRY_RUN pattern: `BranchPythonOperator` controlled by an Airflow Variable — validate config in CI without launching a real job
- Post-job `BigQueryInsertJobOperator` + `BigQueryCheckOperator` for output validation
- `PubSubPublishMessageOperator` for structured completion events
- Dedicated Dataflow worker service account with least-privilege IAM roles

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export COMPOSER_ENV=your-composer-env
export COMPOSER_BUCKET=your-composer-bucket   # gs:// bucket auto-created by Composer
export GCS_BUCKET=your-gcs-bucket
export BQ_DATASET=your_dataset
export PUBSUB_TOPIC=your-pubsub-topic
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

The script:
1. Enables Dataflow, Composer, BigQuery, Artifact Registry, Cloud Build APIs
2. Creates Artifact Registry Docker repo for the Flex Template container image
3. Creates GCS bucket with `dataflow/temp/`, `dataflow/staging/`, `dataflow/templates/` prefixes
4. Creates BigQuery dataset
5. Creates Pub/Sub topic
6. Creates a dedicated Dataflow worker service account with appropriate IAM roles
7. Prints the `docker build` + `gcloud dataflow flex-template build` commands to build your Flex Template
8. Creates Composer 2 environment (`--async`)

## Build the Flex Template (before running the DAG)

```bash
# Step 1: Build and push the container image
gcloud builds submit flex_template/ \
  --tag "us-central1-docker.pkg.dev/your-project-id/dataflow-templates/sales-etl:latest"

# Step 2: Build the Flex Template spec JSON
gcloud dataflow flex-template build \
  "gs://your-gcs-bucket/dataflow/templates/sales-etl-template.json" \
  --image "us-central1-docker.pkg.dev/your-project-id/dataflow-templates/sales-etl:latest" \
  --sdk-language PYTHON \
  --metadata-file flex_template/metadata.json
```

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
VARS=(
  "project_id=your-project-id"
  "gcp_region=us-central1"
  "gcs_bucket=your-gcs-bucket"
  "bq_dataset=your_dataset"
  "bq_location=US"
  "pubsub_topic=your-pubsub-topic"
  "dataflow_flex_template_path=gs://your-gcs-bucket/dataflow/templates/sales-etl-template.json"
  "dataflow_dry_run=false"
)
for KV in "${VARS[@]}"; do
  KEY="${KV%%=*}"; VAL="${KV##*=}"
  gcloud composer environments run "$COMPOSER_ENV" \
    --location "$GOOGLE_CLOUD_LOCATION" \
    variables -- set "$KEY" "$VAL"
done
```

## Run

```bash
# DRY_RUN first — validates all parameters without launching a real Dataflow job
gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  variables -- set dataflow_dry_run true

gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  dags trigger -- dataflow_orchestration_dag

# Switch to live run
gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  variables -- set dataflow_dry_run false

gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  dags trigger -- dataflow_orchestration_dag

# Run the operator reference demo
gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  dags trigger -- dataflow_flex_template_dag
```

## Files

| File | Purpose |
|---|---|
| `dags/dataflow_orchestration_dag.py` | Full orchestration: preflight → launch → monitor → BQ validate → Pub/Sub |
| `dags/dataflow_flex_template_dag.py` | Operator reference: minimal vs production Flex Template config |
| `data/pipeline_config.json` | Sample Flex Template launch parameters (reference/docs) |
| `deploy.sh` | Artifact Registry + GCS + BQ + Pub/Sub + SA + Composer setup |
| `requirements.txt` | Airflow provider dependencies |

## IAM Roles Required

| Principal | Role | Purpose |
|---|---|---|
| Composer SA | `roles/dataflow.admin` | Launch Dataflow jobs |
| Composer SA | `roles/iam.serviceAccountUser` | Impersonate Dataflow worker SA |
| Dataflow worker SA | `roles/dataflow.worker` | Run Dataflow workers |
| Dataflow worker SA | `roles/bigquery.dataEditor` | Write to BigQuery output table |
| Dataflow worker SA | `roles/bigquery.jobUser` | Create BQ jobs |
| Dataflow worker SA | `roles/storage.objectAdmin` | Read input / write temp files |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
