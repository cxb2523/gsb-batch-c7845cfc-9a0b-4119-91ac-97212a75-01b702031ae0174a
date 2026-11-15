-- =============================================================================
-- 05_query_patterns.sql — Common query patterns against SCD2 table
-- Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
-- =============================================================================

-- ─── 1. Current state of all customers ──────────────────────────────────────
-- Most common query — uses CLUSTER BY is_current for fast scan
SELECT
    customer_id,
    name,
    email,
    city,
    plan,
    monthly_spend,
    valid_from AS customer_since
FROM `your_project.your_dataset.customers_scd2`
WHERE is_current = TRUE
ORDER BY customer_id;


-- ─── 2. Full history for a single customer ───────────────────────────────────
SELECT
    customer_id,
    name,
    city,
    plan,
    monthly_spend,
    valid_from,
    COALESCE(valid_to, TIMESTAMP('9999-12-31')) AS valid_to,
    is_current
FROM `your_project.your_dataset.customers_scd2`
WHERE customer_id = 'C001'
ORDER BY valid_from ASC;


-- ─── 3. Point-in-time snapshot (as-of query) ────────────────────────────────
-- "What did the customer table look like on 2024-06-01?"
DECLARE snapshot_ts TIMESTAMP DEFAULT TIMESTAMP('2024-06-01 00:00:00 UTC');

SELECT
    customer_id,
    name,
    city,
    plan,
    monthly_spend,
    valid_from,
    valid_to
FROM `your_project.your_dataset.customers_scd2`
WHERE valid_from <= snapshot_ts
  AND (valid_to > snapshot_ts OR valid_to IS NULL)
ORDER BY customer_id;


-- ─── 4. Count of changes per customer (churn / activity signal) ─────────────
SELECT
    customer_id,
    name,
    COUNT(*)           AS total_versions,
    MIN(valid_from)    AS first_seen,
    MAX(valid_from)    AS last_change,
    ARRAY_AGG(plan ORDER BY valid_from) AS plan_history
FROM `your_project.your_dataset.customers_scd2`
GROUP BY customer_id, name
ORDER BY total_versions DESC;


-- ─── 5. Customers who upgraded their plan ────────────────────────────────────
WITH plan_changes AS (
    SELECT
        customer_id,
        plan,
        valid_from,
        LAG(plan) OVER (PARTITION BY customer_id ORDER BY valid_from) AS previous_plan
    FROM `your_project.your_dataset.customers_scd2`
)
SELECT
    customer_id,
    previous_plan,
    plan AS new_plan,
    valid_from AS upgrade_date
FROM plan_changes
WHERE previous_plan IS NOT NULL
  AND previous_plan != plan
ORDER BY upgrade_date DESC;


-- ─── 6. SCD1 vs SCD2 comparison — same customer, different views ─────────────
-- SCD1 (current only)
SELECT 'SCD1' AS type, customer_id, city, plan FROM `your_project.your_dataset.customers_scd1`
WHERE customer_id = 'C001'
UNION ALL
-- SCD2 (all versions)
SELECT 'SCD2' AS type, customer_id, city, plan FROM `your_project.your_dataset.customers_scd2`
WHERE customer_id = 'C001'
ORDER BY type, plan;
