-- ══════════════════════════════════════════════════════════════════════
-- Post-Deployment Permissions — Run once after `databricks bundle deploy`
-- ══════════════════════════════════════════════════════════════════════
-- Executed by CI/CD (deploy.yml) using:
--   databricks sql execute --warehouse-id $WAREHOUSE_ID --file scripts/apply_grants.sql
--
-- SECURITY MODEL (Group-Based, Least-Privilege):
--   datavault-admins    → ALL PRIVILEGES on catalog (SP + workspace admins)
--   datavault-engineers  → SELECT on all 5 schemas (debugging/development)
--   datavault-analysts   → SELECT on marts only (dashboard viewers, BI)
--
-- PREREQUISITES:
--   Groups must be created at ACCOUNT level (not workspace level).
--   Unity Catalog only recognizes account-level groups.
--   Create via: Account Console → User Management → Groups
--   Or via API: POST {workspace}/api/2.0/account/scim/v2/Groups
-- ══════════════════════════════════════════════════════════════════════

-- ── datavault-admins: Full control ─────────────────────────────────
GRANT ALL PRIVILEGES ON CATALOG restaurant_chain_db TO `datavault-admins`;

-- ── datavault-engineers: Read all schemas (debugging/development) ──
GRANT USE CATALOG ON CATALOG restaurant_chain_db TO `datavault-engineers`;
GRANT BROWSE ON CATALOG restaurant_chain_db TO `datavault-engineers`;
GRANT USE SCHEMA ON SCHEMA restaurant_chain_db.`01_bronze` TO `datavault-engineers`;
GRANT SELECT ON SCHEMA restaurant_chain_db.`01_bronze` TO `datavault-engineers`;
GRANT USE SCHEMA ON SCHEMA restaurant_chain_db.staging TO `datavault-engineers`;
GRANT SELECT ON SCHEMA restaurant_chain_db.staging TO `datavault-engineers`;
GRANT USE SCHEMA ON SCHEMA restaurant_chain_db.raw_vault TO `datavault-engineers`;
GRANT SELECT ON SCHEMA restaurant_chain_db.raw_vault TO `datavault-engineers`;
GRANT USE SCHEMA ON SCHEMA restaurant_chain_db.business_vault TO `datavault-engineers`;
GRANT SELECT ON SCHEMA restaurant_chain_db.business_vault TO `datavault-engineers`;
GRANT USE SCHEMA ON SCHEMA restaurant_chain_db.marts TO `datavault-engineers`;
GRANT SELECT ON SCHEMA restaurant_chain_db.marts TO `datavault-engineers`;

-- ── datavault-analysts: Read marts only (dashboards/BI) ────────────
GRANT USE CATALOG ON CATALOG restaurant_chain_db TO `datavault-analysts`;
GRANT BROWSE ON CATALOG restaurant_chain_db TO `datavault-analysts`;
GRANT USE SCHEMA ON SCHEMA restaurant_chain_db.marts TO `datavault-analysts`;
GRANT SELECT ON SCHEMA restaurant_chain_db.marts TO `datavault-analysts`;

-- ── account users: Catalog discovery + marts read (safety net) ─────
-- Allows any workspace user to browse the catalog and query marts.
-- Remove these for stricter environments where only group members should access.
GRANT USE CATALOG ON CATALOG restaurant_chain_db TO `account users`;
GRANT BROWSE ON CATALOG restaurant_chain_db TO `account users`;
GRANT USE SCHEMA ON SCHEMA restaurant_chain_db.marts TO `account users`;
GRANT SELECT ON SCHEMA restaurant_chain_db.marts TO `account users`;
