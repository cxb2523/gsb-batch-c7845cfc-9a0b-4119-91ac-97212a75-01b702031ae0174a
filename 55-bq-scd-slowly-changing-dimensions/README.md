# 55. BigQuery SCD — Slowly Changing Dimensions (Type 1, 2 & 3)

> Full implementation of SCD Type 1, 2, and 3 patterns in BigQuery SQL — with MERGE statements, hash-based change detection, point-in-time queries, and history analytics. **GCP Services:** BigQuery

## Architecture

```
customers_initial.csv ──► customers_staging
         +                       │
customers_updates.csv            ├──► SCD Type 1 MERGE ──► customers_scd1  (current only)
                                 │
                                 ├──► SCD Type 2 MERGE ──► customers_scd2  (full history)
                                 │         ├── Step 1: CLOSE changed rows (valid_to, is_current=FALSE)
                                 │         └── Step 2: INSERT new version (valid_from, is_current=TRUE)
                                 │
                                 └──► SCD Type 3 MERGE ──► customers_scd3  (previous column)
```

## What this demonstrates

- **SCD Type 1** — overwrite on change, no history (MERGE with UPDATE + INSERT)
- **SCD Type 2** — full history with `valid_from` / `valid_to` / `is_current` window, surrogate key, MD5 hash-based change detection
- **SCD Type 3** — previous value stored as a column (`previous_city`, `previous_plan`)
- **Query patterns** — current state, full history, point-in-time snapshots, plan upgrade detection, SCD1 vs SCD2 comparison
- **BigQuery best practices** — PARTITION BY `valid_from`, CLUSTER BY `customer_id, is_current`, hash-based change detection to avoid false positives

## SCD Type Comparison

| | Type 1 | Type 2 | Type 3 |
|---|--------|--------|--------|
| Stores history | ❌ No | ✅ Full | ⚠️ One prior value |
| Row count grows | ❌ No | ✅ Yes | ❌ No |
| Point-in-time query | ❌ No | ✅ Yes | ❌ No |
| Complexity | Low | Medium | Low |
| Use when | History doesn't matter | Audit trail / compliance | Only last change matters |

## SQL Files

| File | What it does |
|------|-------------|
| `sql/01_create_tables.sql` | Creates staging, scd1, scd2, scd3 tables with partitioning + clustering |
| `sql/02_scd1_merge.sql` | SCD Type 1 — MERGE that overwrites changed rows |
| `sql/03_scd2_merge.sql` | SCD Type 2 — two-step MERGE: close old row → insert new version |
| `sql/04_scd3_merge.sql` | SCD Type 3 — MERGE that shifts current → previous column |
| `sql/05_query_patterns.sql` | 6 common query patterns: current state, history, point-in-time, upgrades |

## Data Files

| File | Description |
|------|-------------|
| `data/customers_initial.csv` | 5 customers — initial load |
| `data/customers_updates.csv` | 3 records — 2 updates (city/plan/email changes) + 1 new customer |

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id   # ← replace
export BQ_DATASET=your_dataset                # ← replace
export GOOGLE_CLOUD_LOCATION=US
```

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

## How to run the SQLs

You can run these SQL files in multiple ways — pick what fits your stack:

### Option 1 — BigQuery Console
Open each SQL file and run directly in the [BigQuery Console](https://console.cloud.google.com/bigquery).
Replace `your_project.your_dataset` with your actual project and dataset before running.

### Option 2 — bq CLI
```bash
# Replace placeholders first
sed 's/your_project/my-project/g; s/your_dataset/analytics/g' sql/01_create_tables.sql \
  | bq query --use_legacy_sql=false --project_id=$GOOGLE_CLOUD_PROJECT

sed 's/your_project/my-project/g; s/your_dataset/analytics/g' sql/02_scd1_merge.sql \
  | bq query --use_legacy_sql=false --project_id=$GOOGLE_CLOUD_PROJECT
```

### Option 3 — Python (google-cloud-bigquery)
```python
from google.cloud import bigquery
client = bigquery.Client()

with open("sql/03_scd2_merge.sql") as f:
    sql = f.read().replace("your_project", "my-project").replace("your_dataset", "analytics")

client.query(sql).result()
```

### Option 4 — BigQuery Stored Procedures
```sql
CREATE OR REPLACE PROCEDURE `your_project.your_dataset.run_scd2_merge`()
BEGIN
  -- paste contents of sql/03_scd2_merge.sql here
END;

CALL `your_project.your_dataset.run_scd2_merge`();
```

### Option 5 — Dataform
```js
// definitions/customers_scd2.sqlx
config {
  type: "incremental",
  schema: "your_dataset",
  bigquery: {
    partitionBy: "DATE(valid_from)",
    clusterBy: ["customer_id", "is_current"]
  }
}
-- paste SCD2 MERGE logic here, use ${self()} for table reference
```

### Option 6 — dbt
```sql
-- models/customers_scd2.sql
-- Use dbt-bigquery snapshot or custom incremental model
-- dbt snapshot with strategy: timestamp or check
{% snapshot customers_scd2 %}
  {{ config(
      target_schema='your_dataset',
      unique_key='customer_id',
      strategy='check',
      check_cols=['city', 'plan', 'email', 'monthly_spend'],
      invalidate_hard_deletes=False
  ) }}
  SELECT * FROM {{ source('raw', 'customers_staging') }}
{% endsnapshot %}
```

### Option 7 — Cloud Composer / Airflow
```python
from airflow.providers.google.cloud.operators.bigquery import BigQueryInsertJobOperator

scd2_merge = BigQueryInsertJobOperator(
    task_id="scd2_merge",
    configuration={
        "query": {
            "query": open("sql/03_scd2_merge.sql").read(),
            "useLegacySql": False,
        }
    },
)
```

## Run order

```
1. 01_create_tables.sql    — create all tables
2. Load data/customers_initial.csv → customers_staging (via deploy.sh or Console)
3. 02_scd1_merge.sql       — run SCD1
4. 03_scd2_merge.sql       — run SCD2 (initial load)
5. 04_scd3_merge.sql       — run SCD3
6. Load data/customers_updates.csv → customers_staging (second batch)
7. Re-run 02, 03, 04       — observe how each SCD handles changes differently
8. 05_query_patterns.sql   — explore history, point-in-time, upgrades
```

## Key concepts

**Surrogate key** — `MD5(customer_id + valid_from)` — uniquely identifies each version of a record.

**Record hash** — `MD5(name + email + city + plan + monthly_spend)` — used to detect changes without column-by-column comparison. Only recompute the merge when hash differs.

**Two-step SCD2 MERGE** — BigQuery does not support `WHEN MATCHED THEN INSERT` in a single MERGE statement. The two-step pattern (close → insert) is the standard BigQuery SCD2 approach.

**Partitioning** — `PARTITION BY DATE(valid_from)` keeps historical queries fast even as the table grows to billions of rows.

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
