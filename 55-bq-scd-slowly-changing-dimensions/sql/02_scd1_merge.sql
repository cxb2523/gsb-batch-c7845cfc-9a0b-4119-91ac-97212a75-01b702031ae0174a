-- =============================================================================
-- 02_scd1_merge.sql — SCD Type 1 MERGE (overwrite on change)
-- Matched rows: UPDATE all tracked columns
-- New rows: INSERT
-- No history is kept — only current state
-- Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
-- =============================================================================

MERGE `your_project.your_dataset.customers_scd1` AS target
USING (
    SELECT
        customer_id,
        name,
        email,
        city,
        plan,
        monthly_spend,
        load_timestamp
    FROM `your_project.your_dataset.customers_staging`
) AS source
ON target.customer_id = source.customer_id

-- Existing customer changed — overwrite
WHEN MATCHED AND (
    target.name          != source.name   OR
    target.email         != source.email  OR
    target.city          != source.city   OR
    target.plan          != source.plan   OR
    target.monthly_spend != source.monthly_spend
) THEN UPDATE SET
    name          = source.name,
    email         = source.email,
    city          = source.city,
    plan          = source.plan,
    monthly_spend = source.monthly_spend,
    updated_at    = source.load_timestamp

-- New customer — insert
WHEN NOT MATCHED BY TARGET THEN INSERT (
    customer_id, name, email, city, plan, monthly_spend, updated_at
) VALUES (
    source.customer_id,
    source.name,
    source.email,
    source.city,
    source.plan,
    source.monthly_spend,
    source.load_timestamp
);
