-- =============================================================================
-- 03_scd2_merge.sql — SCD Type 2 MERGE (full history with validity window)
--
-- Strategy (two-step MERGE):
--   Step 1: CLOSE existing current rows where tracked columns changed
--           Set valid_to = load_timestamp, is_current = FALSE
--   Step 2: INSERT new rows for changed + new customers
--           Set valid_from = load_timestamp, valid_to = NULL, is_current = TRUE
--
-- Change detection: MD5 hash of all tracked columns
-- Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
-- =============================================================================

-- ─── STEP 1: Close changed records ──────────────────────────────────────────
MERGE `your_project.your_dataset.customers_scd2` AS target
USING (
    SELECT
        s.customer_id,
        s.name,
        s.email,
        s.city,
        s.plan,
        s.monthly_spend,
        s.load_timestamp,
        TO_HEX(MD5(CONCAT(
            COALESCE(s.name,          ''),
            COALESCE(s.email,         ''),
            COALESCE(s.city,          ''),
            COALESCE(s.plan,          ''),
            COALESCE(CAST(s.monthly_spend AS STRING), '')
        ))) AS record_hash
    FROM `your_project.your_dataset.customers_staging` s
) AS source
ON  target.customer_id = source.customer_id
AND target.is_current   = TRUE

-- Change detected — close the current row
WHEN MATCHED AND target.record_hash != source.record_hash
THEN UPDATE SET
    valid_to   = source.load_timestamp,
    is_current = FALSE;


-- ─── STEP 2: Insert new / changed rows ──────────────────────────────────────
INSERT INTO `your_project.your_dataset.customers_scd2` (
    surrogate_key,
    customer_id,
    name,
    email,
    city,
    plan,
    monthly_spend,
    valid_from,
    valid_to,
    is_current,
    record_hash,
    created_at
)
SELECT
    TO_HEX(MD5(CONCAT(s.customer_id, CAST(s.load_timestamp AS STRING)))),
    s.customer_id,
    s.name,
    s.email,
    s.city,
    s.plan,
    s.monthly_spend,
    s.load_timestamp  AS valid_from,
    NULL              AS valid_to,       -- open / current record
    TRUE              AS is_current,
    TO_HEX(MD5(CONCAT(
        COALESCE(s.name,          ''),
        COALESCE(s.email,         ''),
        COALESCE(s.city,          ''),
        COALESCE(s.plan,          ''),
        COALESCE(CAST(s.monthly_spend AS STRING), '')
    )))               AS record_hash,
    CURRENT_TIMESTAMP AS created_at
FROM `your_project.your_dataset.customers_staging` s
-- Insert if: brand new customer OR their current row was just closed in Step 1
WHERE NOT EXISTS (
    SELECT 1
    FROM `your_project.your_dataset.customers_scd2` t
    WHERE t.customer_id = s.customer_id
    AND   t.is_current  = TRUE
);
