-- Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
-- YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com
--
-- validate.sql
-- Purpose: Row count validation used by BigQueryCheckOperator.
--
-- BigQueryCheckOperator evaluates the FIRST column of the FIRST row.
-- If the value is falsy (0, NULL, empty string) the task FAILS.
-- Return 1 (pass) when all checks pass, else 0 (fail).
--
-- Jinja parameters:
--   {{ params.project_id }}      — GCP project ID
--   {{ params.dataset }}         — BigQuery dataset
--   {{ ds }}                     — Airflow logical date (YYYY-MM-DD)
--   {{ params.min_row_count }}   — Minimum expected rows (default: 1)

SELECT
    CASE
        -- Check 1: staging table has rows for this partition
        WHEN staging_count < {{ params.min_row_count }}
        THEN 0

        -- Check 2: summary table has rows for this partition
        WHEN summary_count < 1
        THEN 0

        -- Check 3: no negative revenue in summary
        WHEN negative_revenue_count > 0
        THEN 0

        -- Check 4: staging row count >= summary row count
        --          (summary aggregates, so staging should always have >= rows)
        WHEN staging_count < summary_count
        THEN 0

        -- All checks passed
        ELSE 1
    END AS validation_passed,

    -- Include detail columns for debugging in Airflow logs
    staging_count,
    summary_count,
    negative_revenue_count

FROM (
    SELECT
        (
            SELECT COUNT(*)
            FROM `{{ params.project_id }}.{{ params.dataset }}.sales_staging`
            WHERE partition_date = '{{ ds }}'
        ) AS staging_count,

        (
            SELECT COUNT(*)
            FROM `{{ params.project_id }}.{{ params.dataset }}.sales_summary`
            WHERE report_date = '{{ ds }}'
        ) AS summary_count,

        (
            SELECT COUNT(*)
            FROM `{{ params.project_id }}.{{ params.dataset }}.sales_summary`
            WHERE report_date = '{{ ds }}'
              AND (gross_revenue < 0 OR net_revenue < 0)
        ) AS negative_revenue_count
)
