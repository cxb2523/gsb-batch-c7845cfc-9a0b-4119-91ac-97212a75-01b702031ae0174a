-- Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
-- YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com
--
-- staging_load.sql
-- Purpose: MERGE raw GCS-loaded data into the staging table.
--          Idempotent — safe to re-run for the same logical date.
--
-- Jinja parameters (injected by BigQueryInsertJobOperator):
--   {{ params.project_id }}   — GCP project ID
--   {{ params.dataset }}      — BigQuery dataset
--   {{ ds }}                  — Airflow logical date (YYYY-MM-DD)
--   {{ ds_nodash }}           — Logical date without dashes (YYYYMMDD)

MERGE `{{ params.project_id }}.{{ params.dataset }}.sales_staging` AS target
USING (
    SELECT
        CAST(order_id       AS INT64)    AS order_id,
        PARSE_DATE('%Y-%m-%d', order_date) AS order_date,
        customer_id,
        customer_name,
        region,
        category,
        product_name,
        CAST(quantity       AS INT64)    AS quantity,
        CAST(unit_price     AS FLOAT64)  AS unit_price,
        CAST(total_amount   AS FLOAT64)  AS total_amount,
        status,
        CAST(discount_pct   AS FLOAT64)  AS discount_pct,
        '{{ ds }}'                       AS partition_date,
        CURRENT_TIMESTAMP()              AS ingested_at
    FROM `{{ params.project_id }}.{{ params.dataset }}.sales_raw_{{ ds_nodash }}`
    WHERE order_id IS NOT NULL
      AND total_amount > 0
) AS source
ON target.order_id = source.order_id
   AND target.partition_date = source.partition_date

-- Update existing rows (handles re-delivery / late-arriving corrections)
WHEN MATCHED THEN UPDATE SET
    target.order_date    = source.order_date,
    target.customer_id   = source.customer_id,
    target.customer_name = source.customer_name,
    target.region        = source.region,
    target.category      = source.category,
    target.product_name  = source.product_name,
    target.quantity      = source.quantity,
    target.unit_price    = source.unit_price,
    target.total_amount  = source.total_amount,
    target.status        = source.status,
    target.discount_pct  = source.discount_pct,
    target.ingested_at   = source.ingested_at

-- Insert new rows
WHEN NOT MATCHED THEN INSERT (
    order_id, order_date, customer_id, customer_name,
    region, category, product_name,
    quantity, unit_price, total_amount,
    status, discount_pct, partition_date, ingested_at
)
VALUES (
    source.order_id, source.order_date, source.customer_id, source.customer_name,
    source.region, source.category, source.product_name,
    source.quantity, source.unit_price, source.total_amount,
    source.status, source.discount_pct, source.partition_date, source.ingested_at
);
