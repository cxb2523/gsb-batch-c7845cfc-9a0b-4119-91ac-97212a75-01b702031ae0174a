-- ================================================================
-- Data Vault 2.1 — Unity Catalog Governance: Tags & Column Comments
-- Run ONCE after initial pipeline deployment to apply governance metadata.
-- ================================================================

-- ────────────────────────────────────────────────────────────────
-- 1. SCHEMA-LEVEL TAGS (layer + data owner)
-- ────────────────────────────────────────────────────────────────
ALTER SCHEMA restaurant_chain_db.`01_bronze`        SET TAGS ('layer' = 'bronze',         'data_owner' = 'data-engineering');
ALTER SCHEMA restaurant_chain_db.staging            SET TAGS ('layer' = 'staging',        'data_owner' = 'data-engineering');
ALTER SCHEMA restaurant_chain_db.raw_vault          SET TAGS ('layer' = 'raw_vault',      'data_owner' = 'data-engineering');
ALTER SCHEMA restaurant_chain_db.business_vault     SET TAGS ('layer' = 'business_vault', 'data_owner' = 'data-engineering');
ALTER SCHEMA restaurant_chain_db.marts              SET TAGS ('layer' = 'marts',          'data_owner' = 'analytics');

-- ────────────────────────────────────────────────────────────────
-- 2. TABLE-LEVEL TAGS (PII + data classification)
-- ────────────────────────────────────────────────────────────────

-- Bronze: source data with PII
ALTER TABLE restaurant_chain_db.`01_bronze`.customers    SET TAGS ('pii' = 'masked_sha256', 'data_classification' = 'confidential');
ALTER TABLE restaurant_chain_db.`01_bronze`.orders       SET TAGS ('pii' = 'false',         'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.`01_bronze`.restaurants   SET TAGS ('pii' = 'false',         'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.`01_bronze`.menu_items   SET TAGS ('pii' = 'false',         'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.`01_bronze`.reviews      SET TAGS ('pii' = 'false',         'data_classification' = 'internal');

-- Raw Vault: satellites with descriptive attributes
ALTER TABLE restaurant_chain_db.raw_vault.sat_customer       SET TAGS ('pii' = 'masked_sha256', 'data_classification' = 'confidential');
ALTER TABLE restaurant_chain_db.raw_vault.sat_review         SET TAGS ('pii' = 'potential',     'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.raw_vault.sat_order_header   SET TAGS ('pii' = 'false',         'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.raw_vault.sat_restaurant     SET TAGS ('pii' = 'false',         'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.raw_vault.sat_menu_item      SET TAGS ('pii' = 'false',         'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.raw_vault.sat_order_item     SET TAGS ('pii' = 'false',         'data_classification' = 'internal');

-- Hubs: business keys only
ALTER TABLE restaurant_chain_db.raw_vault.hub_customer       SET TAGS ('pii' = 'false', 'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.raw_vault.hub_restaurant     SET TAGS ('pii' = 'false', 'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.raw_vault.hub_menu_item      SET TAGS ('pii' = 'false', 'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.raw_vault.hub_order          SET TAGS ('pii' = 'false', 'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.raw_vault.hub_review         SET TAGS ('pii' = 'false', 'data_classification' = 'internal');

-- Business Vault
ALTER TABLE restaurant_chain_db.business_vault.pit_customer  SET TAGS ('pii' = 'masked_sha256', 'data_classification' = 'confidential');
ALTER TABLE restaurant_chain_db.business_vault.ref_record_source SET TAGS ('pii' = 'false', 'data_classification' = 'public');

-- Marts: consumption layer
ALTER TABLE restaurant_chain_db.marts.dim_customer           SET TAGS ('pii' = 'masked_sha256', 'data_classification' = 'confidential');
ALTER TABLE restaurant_chain_db.marts.fact_order             SET TAGS ('pii' = 'false',         'data_classification' = 'internal');
ALTER TABLE restaurant_chain_db.marts.feat_customer_features SET TAGS ('pii' = 'false',         'data_classification' = 'internal');

-- ────────────────────────────────────────────────────────────────
-- 3. COLUMN-LEVEL PII TAGS (on bronze customers — source of PII)
-- ────────────────────────────────────────────────────────────────
ALTER TABLE restaurant_chain_db.`01_bronze`.customers ALTER COLUMN name  SET TAGS ('pii_type' = 'person_name', 'masking' = 'sha256');
ALTER TABLE restaurant_chain_db.`01_bronze`.customers ALTER COLUMN email SET TAGS ('pii_type' = 'email',       'masking' = 'sha256');
ALTER TABLE restaurant_chain_db.`01_bronze`.customers ALTER COLUMN phone SET TAGS ('pii_type' = 'phone',       'masking' = 'sha256');

-- Propagated PII columns in raw vault satellite
ALTER TABLE restaurant_chain_db.raw_vault.sat_customer ALTER COLUMN name  SET TAGS ('pii_type' = 'person_name', 'masking' = 'sha256');
ALTER TABLE restaurant_chain_db.raw_vault.sat_customer ALTER COLUMN email SET TAGS ('pii_type' = 'email',       'masking' = 'sha256');
ALTER TABLE restaurant_chain_db.raw_vault.sat_customer ALTER COLUMN phone SET TAGS ('pii_type' = 'phone',       'masking' = 'sha256');

-- Review text may contain PII (customer names mentioned in reviews)
ALTER TABLE restaurant_chain_db.raw_vault.sat_review ALTER COLUMN review_text SET TAGS ('pii_type' = 'free_text', 'masking' = 'none');

-- ────────────────────────────────────────────────────────────────
-- 4. COLUMN COMMENTS — Key tables (hubs, satellites, facts)
-- ────────────────────────────────────────────────────────────────

-- Hub Customer
COMMENT ON COLUMN restaurant_chain_db.raw_vault.hub_customer.hk_customer    IS 'SHA-256 hash key of (RESTAURANT_CHAIN || customer_id) — primary key';
COMMENT ON COLUMN restaurant_chain_db.raw_vault.hub_customer.customer_id    IS 'Natural business key from source system';
COMMENT ON COLUMN restaurant_chain_db.raw_vault.hub_customer.load_dts       IS 'Timestamp when this business key was first observed in the vault';
COMMENT ON COLUMN restaurant_chain_db.raw_vault.hub_customer.record_source  IS 'Source system that first introduced this key (see ref_record_source)';

-- Satellite Customer
COMMENT ON COLUMN restaurant_chain_db.raw_vault.sat_customer.hk_customer    IS 'FK to hub_customer — part of composite PK';
COMMENT ON COLUMN restaurant_chain_db.raw_vault.sat_customer.hash_diff      IS 'SHA-256 of all descriptive attributes — change detection key';
COMMENT ON COLUMN restaurant_chain_db.raw_vault.sat_customer.load_dts       IS 'When this attribute version was first observed';
COMMENT ON COLUMN restaurant_chain_db.raw_vault.sat_customer.name           IS 'Customer name (PII: SHA-256 masked at bronze layer)';
COMMENT ON COLUMN restaurant_chain_db.raw_vault.sat_customer.email          IS 'Customer email (PII: SHA-256 masked at bronze layer)';
COMMENT ON COLUMN restaurant_chain_db.raw_vault.sat_customer.phone          IS 'Customer phone (PII: SHA-256 masked at bronze layer)';

-- Fact Order
COMMENT ON COLUMN restaurant_chain_db.marts.fact_order.order_id             IS 'Natural business key for the order';
COMMENT ON COLUMN restaurant_chain_db.marts.fact_order.order_date           IS 'Date of order placement (derived from order_timestamp)';
COMMENT ON COLUMN restaurant_chain_db.marts.fact_order.total_amount         IS 'Total order value in AED';
COMMENT ON COLUMN restaurant_chain_db.marts.fact_order.item_count           IS 'Number of line items in the order';
COMMENT ON COLUMN restaurant_chain_db.marts.fact_order.is_weekend           IS 'Boolean: true if order placed on Saturday or Sunday';

-- Feature Table
COMMENT ON COLUMN restaurant_chain_db.marts.feat_customer_features.recency_days        IS 'Days since last order (RFM recency component)';
COMMENT ON COLUMN restaurant_chain_db.marts.feat_customer_features.frequency           IS 'Total number of orders placed (RFM frequency component)';
COMMENT ON COLUMN restaurant_chain_db.marts.feat_customer_features.monetary            IS 'Total lifetime spend in AED (RFM monetary component)';
COMMENT ON COLUMN restaurant_chain_db.marts.feat_customer_features.avg_order_value     IS 'Average order value in AED';
COMMENT ON COLUMN restaurant_chain_db.marts.feat_customer_features.distinct_restaurants IS 'Number of unique restaurants ordered from';
COMMENT ON COLUMN restaurant_chain_db.marts.feat_customer_features.weekend_order_ratio IS 'Proportion of orders placed on weekends (0.0–1.0)';
COMMENT ON COLUMN restaurant_chain_db.marts.feat_customer_features.positive_review_ratio IS 'Proportion of reviews with positive sentiment (0.0–1.0)';

-- Reference Record Source
COMMENT ON COLUMN restaurant_chain_db.business_vault.ref_record_source.record_source        IS 'Unique source system code used across all DV tables';
COMMENT ON COLUMN restaurant_chain_db.business_vault.ref_record_source.data_classification  IS 'Data sensitivity level: public, internal, confidential';
COMMENT ON COLUMN restaurant_chain_db.business_vault.ref_record_source.expected_sla         IS 'Expected data freshness SLA for this source';
