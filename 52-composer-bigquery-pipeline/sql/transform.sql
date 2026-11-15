-- Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
-- YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com
--
-- transform.sql
-- Purpose: Aggregate staged data into sales summary by category and region.
--          Writes to sales_summary (partitioned by report_date).
--          Idempotent — DELETE+INSERT pattern for the logical date partition.
--
-- Jinja parameters:
--   {{ params.project_id }}  — GCP project ID
--   {{ params.dataset }}     — BigQuery dataset
--   {{ ds }}                 — Airflow logical date (YYYY-MM-DD)

-- Step 1: Remove existing rows for this date partition (idempotency)
DELETE FROM `{{ params.project_id }}.{{ params.dataset }}.sales_summary`
WHERE report_date = '{{ ds }}';

-- Step 2: Insert fresh aggregates for this date partition
INSERT INTO `{{ params.project_id }}.{{ params.dataset }}.sales_summary`
(
    report_date,
    region,
    category,
    total_orders,
    completed_orders,
    cancelled_orders,
    pending_orders,
    total_units_sold,
    gross_revenue,
    net_revenue,
    avg_order_value,
    avg_discount_pct,
    top_product,
    created_at
)
WITH base AS (
    SELECT
        partition_date                                          AS report_date,
        region,
        category,
        status,
        order_id,
        quantity,
        total_amount,
        total_amount * (1 - discount_pct / 100.0)             AS net_amount,
        discount_pct,
        product_name
    FROM `{{ params.project_id }}.{{ params.dataset }}.sales_staging`
    WHERE partition_date = '{{ ds }}'
),

aggregated AS (
    SELECT
        report_date,
        region,
        category,
        COUNT(DISTINCT order_id)                              AS total_orders,
        COUNTIF(status = 'completed')                        AS completed_orders,
        COUNTIF(status = 'cancelled')                        AS cancelled_orders,
        COUNTIF(status = 'pending')                          AS pending_orders,
        SUM(quantity)                                        AS total_units_sold,
        ROUND(SUM(total_amount), 2)                          AS gross_revenue,
        ROUND(SUM(net_amount), 2)                            AS net_revenue,
        ROUND(AVG(total_amount), 2)                          AS avg_order_value,
        ROUND(AVG(discount_pct), 2)                          AS avg_discount_pct
    FROM base
    GROUP BY report_date, region, category
),

top_products AS (
    SELECT
        report_date,
        region,
        category,
        product_name,
        SUM(total_amount) AS product_revenue,
        ROW_NUMBER() OVER (
            PARTITION BY report_date, region, category
            ORDER BY SUM(total_amount) DESC
        ) AS rn
    FROM base
    GROUP BY report_date, region, category, product_name
)

SELECT
    a.report_date,
    a.region,
    a.category,
    a.total_orders,
    a.completed_orders,
    a.cancelled_orders,
    a.pending_orders,
    a.total_units_sold,
    a.gross_revenue,
    a.net_revenue,
    a.avg_order_value,
    a.avg_discount_pct,
    tp.product_name                                          AS top_product,
    CURRENT_TIMESTAMP()                                      AS created_at
FROM aggregated a
LEFT JOIN top_products tp
    ON  a.report_date = tp.report_date
    AND a.region      = tp.region
    AND a.category    = tp.category
    AND tp.rn         = 1
ORDER BY a.region, a.category;
