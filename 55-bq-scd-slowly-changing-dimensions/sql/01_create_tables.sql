-- =============================================================================
-- 01_create_tables.sql
-- Creates staging, SCD1 (current state), and SCD2 (history) tables
-- Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
-- =============================================================================

-- Staging table: raw incoming data (always overwritten per load)
CREATE OR REPLACE TABLE `your_project.your_dataset.customers_staging` (
    customer_id     STRING    NOT NULL,
    name            STRING,
    email           STRING,
    city            STRING,
    plan            STRING,
    monthly_spend   NUMERIC,
    load_timestamp  TIMESTAMP
);

-- =============================================================================
-- SCD TYPE 1 — current state only (no history)
-- Every update overwrites the existing row.
-- Use when: history doesn't matter, only latest value needed
-- =============================================================================
CREATE OR REPLACE TABLE `your_project.your_dataset.customers_scd1` (
    customer_id     STRING    NOT NULL,
    name            STRING,
    email           STRING,
    city            STRING,
    plan            STRING,
    monthly_spend   NUMERIC,
    updated_at      TIMESTAMP
)
CLUSTER BY customer_id;

-- =============================================================================
-- SCD TYPE 2 — full history with validity window
-- Each change creates a new row; old row is closed with valid_to date.
-- Use when: audit trail, point-in-time reporting, compliance
-- =============================================================================
CREATE OR REPLACE TABLE `your_project.your_dataset.customers_scd2` (
    surrogate_key   STRING    NOT NULL,   -- MD5(customer_id || valid_from)
    customer_id     STRING    NOT NULL,
    name            STRING,
    email           STRING,
    city            STRING,
    plan            STRING,
    monthly_spend   NUMERIC,
    valid_from      TIMESTAMP NOT NULL,
    valid_to        TIMESTAMP,            -- NULL means current / open record
    is_current      BOOL      NOT NULL,
    record_hash     STRING,               -- MD5 of tracked columns for change detection
    created_at      TIMESTAMP
)
PARTITION BY DATE(valid_from)
CLUSTER BY customer_id, is_current;

-- =============================================================================
-- SCD TYPE 3 — previous value stored as column alongside current
-- Use when: only ONE prior value matters (e.g. previous city for mailers)
-- =============================================================================
CREATE OR REPLACE TABLE `your_project.your_dataset.customers_scd3` (
    customer_id      STRING    NOT NULL,
    name             STRING,
    email            STRING,
    current_city     STRING,
    previous_city    STRING,              -- only tracks ONE prior value
    current_plan     STRING,
    previous_plan    STRING,
    monthly_spend    NUMERIC,
    updated_at       TIMESTAMP
)
CLUSTER BY customer_id;
