"""Staging · Orders (Header-Level)

Reads the unified bronze orders streaming table and prepares it for
the raw vault by computing hash keys and hash-diff, plus adding DV
metadata columns (load_dts, record_source).

Downstream consumers in raw_vault:
  hub_order, hub_customer (via orders), hub_restaurant (via orders),
  lnk_order, sat_order_header
"""
from pyspark import pipelines as dp
from pyspark.sql.functions import col

from _dv_hash import DV_LOAD_DTS, dv_record_source, hash_diff, hash_key

# ── Schema target ─────────────────────────────────────────────────
CATALOG_SCHEMA = "restaurant_chain_db.staging"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.stg_orders",
    comment="Staged orders with DV hash keys, hash-diff, and audit metadata",
    table_properties={"quality": "staging"},
)
@dp.expect_all_or_fail(
    {
        "valid_order_id": "order_id IS NOT NULL",
        "valid_customer_id": "customer_id IS NOT NULL",
        "valid_restaurant_id": "restaurant_id IS NOT NULL",
    }
)
def stg_orders():
    df = spark.read.table("restaurant_chain_db.01_bronze.orders")

    # ── Hash keys for every hub reachable from this source ────────
    df = hash_key(df, ["order_id"], "hk_order")
    df = hash_key(df, ["customer_id"], "hk_customer")
    df = hash_key(df, ["restaurant_id"], "hk_restaurant")

    # ── Link hash key: order ↔ customer ↔ restaurant ─────────────
    df = hash_key(
        df,
        ["order_id", "customer_id", "restaurant_id"],
        "hk_lnk_order",
    )

    # ── Hash-diff for sat_order_header descriptive attributes ─────
    df = hash_diff(
        df,
        ["order_type", "total_amount", "payment_method", "order_status"],
        "hash_diff_order_header",
    )

    # ── Metadata ──────────────────────────────────────────────────
    df = df.withColumn("load_dts", DV_LOAD_DTS())
    df = df.withColumn("record_source", dv_record_source("BRONZE_ORDERS"))

    return df.select(
        "hk_order", "hk_customer", "hk_restaurant", "hk_lnk_order",
        "hash_diff_order_header",
        "order_id", "customer_id", "restaurant_id",
        "order_timestamp", "order_type", "total_amount",
        "payment_method", "order_status", "source",
        "items",                       # kept for stg_order_items
        "load_dts", "record_source",
    )
