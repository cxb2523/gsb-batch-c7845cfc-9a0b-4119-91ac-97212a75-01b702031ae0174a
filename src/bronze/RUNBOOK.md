# Runbook — Bronze Layer Ingestion Pipeline

## Common Operations

### Regular pipeline run (incremental)
Processes only new EventHub messages. MVs are refreshed from SQL Server.
- Pipeline UI → **Start** (default update)
- Or schedule via a Databricks Job

### Full refresh
Reprocesses ALL data from scratch. Use when:
- Schema changes in source tables
- Historical backfill needs to re-run (the `once=True` flow only fires on full refresh)
- Data corruption requires a clean rebuild

**Warning**: `eventhub_raw` has `pipelines.reset.allowed = false`. A full refresh
will NOT delete EventHub data — it only replays from the configured `startingOffsets`.

## Adding a New SQL Server Table

1. Create `transformations/01_bronze/<table_name>.py`
2. Follow the pattern in `restaurants.py` (simplest example):
   ```python
   from pyspark import pipelines as dp
   from read_sql_server import read_sql_server_table

   @dp.materialized_view(
       name="restaurant_chain_db.01_bronze.<table_name>",
       comment="...",
       table_properties={"quality": "bronze"},
   )
   def <table_name>():
       return read_sql_server_table("<dbo_table_name>", dbutils)
   ```
3. Add expectations matching the table's constraints
4. The file is auto-included via the `transformations/**` glob — no settings change needed

## Shared JDBC Module

`01_bronze/read_sql_server.py` is a utility module imported by all bronze dimension files.
It uses `SparkSession.getActiveSession()` internally because SDP-injected globals (`spark`)
are not available in imported modules. Only `dbutils` is passed as a parameter for secrets.

`00_landing/historical_orders.py` has its own inline JDBC helper because it is in a different
directory and cross-directory imports in SDP pipelines require `sys.path` workarounds.

## Modifying the JDBC Connection

- **Host/Database**: Update `SQL_HOST_NAME` / `SQL_DB_NAME` in pipeline Settings → Configuration
- **Credentials**: Update secrets in the `res-chain-pipeline` Databricks Secret Scope
- **Connection resilience**: Edit retry/timeout options in `read_sql_server.py` (and the
  inline helper in `historical_orders.py`)

## Modifying the EventHub Connection

- **Namespace/Topic**: Update `EH_NAMESPACE` / `EH_NAME` in pipeline Settings → Configuration
- **Connection string**: Update `eh-conn-str` in the `res-chain-pipeline` Secret Scope
- **Batch size**: Adjust `maxOffsetsPerTrigger` in pipeline config (higher = more throughput, more memory)

## Troubleshooting

### `No module named 'read_sql_server'`
The importing file is not in the same directory as `read_sql_server.py`. Same-directory
imports work natively in SDP; cross-directory imports require `sys.path` — use an inline
helper instead (see `historical_orders.py`).

### `name 'spark' is not defined` in read_sql_server.py
This module is imported via standard Python `import`, where SDP globals are unavailable.
All spark access must go through `SparkSession.getActiveSession()` inside function bodies.

### EventHub offset errors / `failOnDataLoss`
`eventhub_raw.py` has `failOnDataLoss = true`. If EventHub retention expires before
the pipeline processes messages, the pipeline fails (by design — prevents silent data loss).
Fix: update `startingOffsets` in pipeline config to `latest`, run once, then revert to `earliest`.

### SQL Server timeout on cold start
Azure SQL Serverless may take up to 60s to resume. The JDBC config includes 3 retries x 10s
intervals + 60s login timeout. If timeouts persist, increase `loginTimeout` in `read_sql_server.py`.

### Changing a dataset type (MV to ST or vice versa)
You **cannot** change a dataset's type in-place. The pipeline will fail.
1. Drop the existing table: `DROP TABLE restaurant_chain_db.<schema>.<table>`
2. Change the decorator/API in the source file
3. Run a full refresh

### Historical backfill re-run
The `orders_from_historical` append flow has `once=True` — it runs only on full refresh.
To re-ingest historical orders, trigger a full refresh. This is safe: `eventhub_raw` will not
lose data due to `pipelines.reset.allowed = false`.

## Edge Cases

### Duplicate EventHub messages
EventHub has at-least-once delivery. Duplicates are now absorbed **inside the
bronze layer** instead of being deferred downstream:

1. a watermarked stateful `dropDuplicates("order_id")` collapses repeats within
   the dedup state window per micro-batch (incremental, no full scan);
2. the `orders` live flow is an idempotent `merge_flow` on `order_id`, so
   redelivery across micro-batches or pipeline restarts updates the existing
   row rather than inserting a second copy.

`orders` therefore holds exactly one row per live `order_id`. The historical
`once=True` append flow is unchanged and not part of the MERGE.

### Unparseable / order_id-less messages
Nothing is silently dropped. Messages that fail `from_json` (reason
`malformed_json`) or parse but have a null/blank `order_id` (reason
`missing_order_id`) are appended to the queryable streaming table
`01_bronze.order_rejections`, including the original `raw_value`,
`event_time`, `ingestion_time` and `rejected_at`. They never enter `orders`.

Inspect recent rejections with:

```sql
SELECT rejection_reason, COUNT(*)
FROM restaurant_chain_db.01_bronze.order_rejections
GROUP BY rejection_reason;

SELECT raw_value, event_time, ingestion_time, rejection_reason
FROM restaurant_chain_db.01_bronze.order_rejections
ORDER BY rejected_at DESC
LIMIT 100;
```

### Reproducing bad/duplicate messages locally
The Event Hub read is injectable. With pipeline configuration
`bronze.eventhub.source=local-file` and `bronze.eventhub.source.path` pointing
at a directory of newline-delimited JSON records, the same streaming logic
runs without the Event Hub connection string or any Databricks secret. The
pytest suite under `tests/` drives valid, malformed, missing-id and duplicate
records through real micro-batches — see `tests/README.md`.

### PII in reviews
`review_text` in the `reviews` table is NOT masked. It may contain customer names, emails,
or other PII embedded in free text. NLP-based redaction is recommended before exposing to analysts.

### Gap in order data
Historical orders cover 2025-07-15 to 2026-01-11. Live EventHub data starts from 2026-04-09.
There is a ~3 month gap (Jan 12 to Apr 8) with no order data. This is expected and documented.
