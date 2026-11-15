-- ================================================================
-- Data Vault 2.1 — Monitoring & Observability Queries
-- Run these in a Databricks SQL editor or schedule as alerts.
-- ================================================================

-- ────────────────────────────────────────────────────────────────
-- QUERY 1: Pipeline Data Quality — Expectation Pass/Fail Metrics
-- Source: system.lakeflow.pipeline_event_log
-- Purpose: Track DQ trends across all 5 pipelines
-- ────────────────────────────────────────────────────────────────

WITH dq_events AS (
  SELECT
    e.origin.pipeline_name,
    e.origin.update_id,
    e.timestamp AS event_time,
    exp.dataset,
    exp.name AS expectation_name,
    exp.passed_records,
    exp.failed_records
  FROM system.lakeflow.pipeline_event_log e
  LATERAL VIEW EXPLODE(
    from_json(
      e.details:flow_progress:data_quality:expectations,
      'array<struct<name: string, dataset: string, passed_records: bigint, failed_records: bigint>>'
    )
  ) AS exp
  WHERE e.event_type = 'flow_progress'
    AND e.origin.pipeline_name LIKE 'datavault_%'
    AND e.timestamp >= current_timestamp() - INTERVAL 7 DAYS
)
SELECT
  pipeline_name,
  dataset,
  expectation_name,
  SUM(passed_records) AS total_passed,
  SUM(failed_records) AS total_failed,
  ROUND(SUM(failed_records) * 100.0 / NULLIF(SUM(passed_records) + SUM(failed_records), 0), 2) AS failure_pct
FROM dq_events
GROUP BY pipeline_name, dataset, expectation_name
HAVING SUM(failed_records) > 0
ORDER BY failure_pct DESC;


-- ────────────────────────────────────────────────────────────────
-- QUERY 2: Data Freshness Monitor
-- Source: information_schema.tables
-- Purpose: Alert if any table hasn't been updated within SLA
-- ────────────────────────────────────────────────────────────────

SELECT
  table_schema,
  table_name,
  last_altered,
  TIMESTAMPDIFF(HOUR, last_altered, current_timestamp()) AS hours_since_update,
  CASE
    WHEN table_schema = '01_bronze' AND TIMESTAMPDIFF(HOUR, last_altered, current_timestamp()) > 2
      THEN 'STALE (SLA: 2h for bronze)'
    WHEN table_schema IN ('staging', 'raw_vault', 'business_vault', 'marts')
      AND TIMESTAMPDIFF(HOUR, last_altered, current_timestamp()) > 26
      THEN 'STALE (SLA: 26h for vault/marts — daily + 2h buffer)'
    ELSE 'OK'
  END AS freshness_status
FROM restaurant_chain_db.information_schema.tables
WHERE table_schema IN ('01_bronze', 'staging', 'raw_vault', 'business_vault', 'marts')
  AND table_type IN ('MANAGED', 'MATERIALIZED_VIEW')
ORDER BY hours_since_update DESC;


-- ────────────────────────────────────────────────────────────────
-- QUERY 3: Table Row Count Summary
-- Purpose: Quick health check — detect empty or unexpectedly small tables
-- Note: Uses information_schema for efficiency (no full table scans)
-- ────────────────────────────────────────────────────────────────

SELECT
  t.table_schema,
  t.table_name,
  t.table_type,
  ds.row_count,
  ds.size_in_bytes,
  ROUND(ds.size_in_bytes / 1024.0 / 1024.0, 2) AS size_mb,
  CASE
    WHEN ds.row_count = 0 THEN '⚠️ EMPTY'
    WHEN ds.row_count IS NULL THEN '❓ NO STATS'
    ELSE '✅ OK'
  END AS status
FROM restaurant_chain_db.information_schema.tables t
LEFT JOIN (
  SELECT
    table_schema,
    table_name,
    SUM(number_of_rows) AS row_count,
    SUM(data_size_in_bytes) AS size_in_bytes
  FROM restaurant_chain_db.information_schema.table_storage_properties
  GROUP BY table_schema, table_name
) ds ON t.table_schema = ds.table_schema AND t.table_name = ds.table_name
WHERE t.table_schema IN ('01_bronze', 'staging', 'raw_vault', 'business_vault', 'marts')
ORDER BY t.table_schema, t.table_name;


-- ────────────────────────────────────────────────────────────────
-- QUERY 4: Pipeline Run History (Last 7 Days)
-- Source: system.lakeflow.pipeline_event_log
-- Purpose: Track pipeline update success/failure over time
-- ────────────────────────────────────────────────────────────────

SELECT
  origin.pipeline_name,
  origin.update_id,
  MIN(timestamp) AS update_start,
  MAX(timestamp) AS update_end,
  TIMESTAMPDIFF(SECOND, MIN(timestamp), MAX(timestamp)) AS duration_seconds,
  MAX(CASE WHEN event_type = 'update_progress' AND details:update_progress:state = 'COMPLETED' THEN 'SUCCESS' END) AS status,
  MAX(CASE WHEN event_type = 'update_progress' AND details:update_progress:state = 'FAILED' THEN 'FAILED' END) AS failure_status
FROM system.lakeflow.pipeline_event_log
WHERE origin.pipeline_name LIKE 'datavault_%'
  AND timestamp >= current_timestamp() - INTERVAL 7 DAYS
  AND event_type IN ('create_update', 'update_progress')
GROUP BY origin.pipeline_name, origin.update_id
ORDER BY update_start DESC;


-- ────────────────────────────────────────────────────────────────
-- QUERY 5: Governance Tag Audit
-- Purpose: Verify all tables have required governance tags applied
-- ────────────────────────────────────────────────────────────────

SELECT
  t.table_schema,
  t.table_name,
  tt.tag_name,
  tt.tag_value
FROM restaurant_chain_db.information_schema.tables t
LEFT JOIN restaurant_chain_db.information_schema.table_tags tt
  ON t.table_schema = tt.schema_name AND t.table_name = tt.table_name
WHERE t.table_schema IN ('01_bronze', 'staging', 'raw_vault', 'business_vault', 'marts')
ORDER BY t.table_schema, t.table_name, tt.tag_name;
