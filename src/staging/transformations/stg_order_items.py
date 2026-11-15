"""Staging · Order Items (Line-Level)

Explodes the nested items array from bronze orders into one row per
order-line, then computes hash keys and hash-diff for the raw vault.

The menu-item business key is the composite (restaurant_id, item_id)
because item_id is restaurant-scoped in the source system.

Downstream consumers in raw_vault:
  hub_menu_item, lnk_order_item, sat_order_item
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

from _dv_hash import DV_LOAD_DTS, dv_record_source, hash_diff, hash_key

CATALOG_SCHEMA = "restaurant_chain_db.staging"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.stg_order_items",
    comment="Staged order line items — exploded from orders.items array with DV hash keys",
    table_properties={"quality": "staging"},
)
@dp.expect_all_or_fail(
    {
        "valid_order_id": "order_id IS NOT NULL",
        "valid_restaurant_id": "restaurant_id IS NOT NULL",
        "valid_item_id": "item_id IS NOT NULL",
    }
)
def stg_order_items():
    df = spark.read.table("restaurant_chain_db.01_bronze.orders")

    # ── Explode nested items array into one row per line ──────────
    df = (
        df.select(
            "order_id",
            "restaurant_id",
            "customer_id",
            "order_timestamp",
            "source",
            F.posexplode("items").alias("line_number", "item"),
        )
        .select(
            "order_id",
            "restaurant_id",
            "customer_id",
            "order_timestamp",
            "source",
            F.col("line_number").cast("int").alias("line_number"),
            F.col("item.item_id").alias("item_id"),
            F.col("item.name").alias("item_name"),
            F.col("item.category").alias("item_category"),
            F.col("item.quantity").alias("quantity"),
            F.col("item.unit_price").alias("unit_price"),
            F.col("item.subtotal").alias("subtotal"),
        )
    )

    # ── Hash keys ─────────────────────────────────────────────────
    df = hash_key(df, ["order_id"], "hk_order")
    df = hash_key(df, ["restaurant_id", "item_id"], "hk_menu_item")
    df = hash_key(df, ["order_id", "restaurant_id", "item_id"], "hk_lnk_order_item")

    # ── Hash-diff for sat_order_item payload ──────────────────────
    df = hash_diff(df, ["quantity", "unit_price", "subtotal"], "hash_diff_order_item")

    # ── Metadata ──────────────────────────────────────────────────
    df = df.withColumn("load_dts", DV_LOAD_DTS())
    df = df.withColumn("record_source", dv_record_source("BRONZE_ORDERS"))

    return df
