# 51. Cloud Composer DAG Basics

> First Composer DAG — task dependencies, BashOperator, PythonOperator, EmptyOperator, XComs, BranchPythonOperator, TaskGroups, and retry logic. **GCP Services:** Cloud Composer 2, Cloud Storage, Cloud Logging.

## Architecture

```
DAG file (.py)
    │
    ▼
gcloud storage cp dags/*.py  ──►  Composer GCS Bucket (gs://auto-generated-bucket/dags/)
                                          │
                                          ▼
                                  Airflow Scheduler
                                  (parses DAGs on schedule)
                                          │
                              ┌───────────┴───────────┐
                              │                       │
                              ▼                       ▼
                        Airflow Worker           Airflow Worker
                     (runs task instances)   (runs task instances)
                              │
                    ┌─────────┴─────────┐
                    │                   │
                    ▼                   ▼
             BashOperator         PythonOperator
          (shell commands,       (Python callables,
          Jinja templating)       XCom push/pull)

basics_dag:
  start → check_environment → create_work_dir → log_run_context
        → process_csv_data → write_output → log_final_summary → end

branching_dag:
  start → branch_by_day ─┬─► run_incremental_load ──┐
                          ├─► run_weekly_aggregation ─┤
                          └─► run_weekend_maintenance ┘
                                                       │
                              after_primary_branch ◄───┘
                                        │
                              branch_quality_level ─┬─► quality_check_strict ──┐
                                                    ├─► quality_check_standard ─┤
                                                    └─► quality_check_minimal ──┘
                                                                                 │
                              after_quality_check ◄──────────────────────────────┘
                                        │
                              log_to_cloud_logging → post_run_summary → end

taskgroup_dag:
  pipeline_start
        │
  [extract]─────────────────────────────────────────────────────┐
  │ extract_from_gcs ─┐                                         │
  │ extract_from_api ─┴─► validate_raw                          │
  └──────────────────────────────────────────────────────────────┘
        │
  [transform]───────────────────────────────────────────────────┐
  │ clean_and_deduplicate → enrich_with_catalog → aggregate     │
  └──────────────────────────────────────────────────────────────┘
        │
  [load]────────────────────────────────────────────────────────┐
  │  [staging]                    [production]                  │
  │  load_to_staging              promote_to_production         │
  │  validate_staging_load  ───►  refresh_materialized_views    │
  └──────────────────────────────────────────────────────────────┘
        │
  send_completion_notification → pipeline_end
```

## What this demonstrates

- `EmptyOperator` as start/end pipeline sentinels
- `BashOperator` with Jinja macros (`{{ ds }}`, `{{ ts }}`, `{{ var.value.* }}`)
- `PythonOperator` for arbitrary Python logic
- XCom `push` / `pull` for inter-task data passing
- `BranchPythonOperator` returning a `task_id` to determine the active branch
- `TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS` to converge multiple branches
- `TaskGroup` to visually organise tasks into logical phases in the Airflow UI
- Nested `TaskGroup` (staging sub-group inside load group)
- Default args with `retries=2` and `retry_delay`
- `schedule="@daily"` with `catchup=False`
- `Variable.get()` for runtime configuration (never hardcoded project IDs)
- `doc_md` on DAG and individual tasks for in-UI documentation

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export COMPOSER_ENV=your-composer-env
export COMPOSER_BUCKET=your-composer-bucket   # gs:// bucket auto-created by Composer
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

> The script enables APIs and creates the Composer 2 environment (`--async`).
> Wait for status `RUNNING` (15–25 min) before uploading DAGs.

## Deploy DAGs

```bash
# Get Composer GCS bucket
export COMPOSER_BUCKET=$(
  gcloud composer environments describe "$COMPOSER_ENV" \
    --location "$GOOGLE_CLOUD_LOCATION" \
    --format='value(config.dagGcsPrefix)'
)

# Upload DAG files to Composer's GCS bucket
gcloud storage cp dags/*.py "$COMPOSER_BUCKET/"

# Upload sample data
gcloud storage cp data/sample_input.csv gs://your-gcs-bucket/landing/sample_input.csv
```

## Airflow Variables

Set these after the environment is `RUNNING`:

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

# Controls branching_dag quality check routing: strict / standard / minimal
gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  variables -- set data_quality_level standard
```

## Run

Trigger via Airflow UI, or:

```bash
# Trigger basics_dag
gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  dags trigger -- basics_dag

# Trigger branching_dag
gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  dags trigger -- branching_dag

# Trigger taskgroup_dag
gcloud composer environments run "$COMPOSER_ENV" \
  --location "$GOOGLE_CLOUD_LOCATION" \
  dags trigger -- taskgroup_dag
```

## Files

| File | Purpose |
|---|---|
| `dags/basics_dag.py` | Linear DAG — BashOperator, PythonOperator, XCom |
| `dags/branching_dag.py` | BranchPythonOperator routing by day of week |
| `dags/taskgroup_dag.py` | TaskGroup-organised ETL with nested sub-groups |
| `data/sample_input.csv` | 20-row sales CSV used by `basics_dag` |
| `deploy.sh` | Create Composer env + upload instructions |
| `requirements.txt` | Airflow provider dependencies |

---

Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
