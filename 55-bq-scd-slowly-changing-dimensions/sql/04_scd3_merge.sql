-- =============================================================================
-- 04_scd3_merge.sql — SCD Type 3 MERGE (previous value as column)
--
-- Tracks ONE prior value per tracked attribute.
-- On change: current_city → previous_city, new value → current_city
-- Limitation: only one level of history. Second change overwrites previous_*.
-- Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
-- =============================================================================

MERGE `your_project.your_dataset.customers_scd3` AS target
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

-- Existing customer with tracked column change — shift current → previous
WHEN MATCHED AND (
    target.current_city != source.city OR
    target.current_plan != source.plan
) THEN UPDATE SET
    previous_city  = target.current_city,
    current_city   = source.city,
    previous_plan  = target.current_plan,
    current_plan   = source.plan,
    name           = source.name,
    email          = source.email,
    monthly_spend  = source.monthly_spend,
    updated_at     = source.load_timestamp

-- No tracked column change — update non-tracked columns only
WHEN MATCHED THEN UPDATE SET
    name          = source.name,
    email         = source.email,
    monthly_spend = source.monthly_spend,
    updated_at    = source.load_timestamp

-- New customer
WHEN NOT MATCHED BY TARGET THEN INSERT (
    customer_id,
    name,
    email,
    current_city,
    previous_city,
    current_plan,
    previous_plan,
    monthly_spend,
    updated_at
) VALUES (
    source.customer_id,
    source.name,
    source.email,
    source.city,
    NULL,             -- no previous value on first insert
    source.plan,
    NULL,
    source.monthly_spend,
    source.load_timestamp
);
