"""Raw Vault · Link — Order

Models the 3-way relationship: order ↔ customer ↔ restaurant.
One row per unique combination of (order_id, customer_id, restaurant_id).

Columns:
  hk_lnk_order   : SHA-256 hash of (SALT || order_id || customer_id || restaurant_id)  — PK
  hk_order        : FK → hub_order
  hk_customer     : FK → hub_customer
  hk_restaurant   : FK → hub_restaurant
  load_dts        : Earliest observed load timestamp
  record_source   : Source that first introduced this relationship
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.lnk_order",
    comment="Link — order ↔ customer ↔ restaurant relationship",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_lnk_order"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_lnk_order IS NOT NULL",
        "pk_valid_hash": "LENGTH(hk_lnk_order) = 64",
        "fk_order_not_null": "hk_order IS NOT NULL",
        "fk_customer_not_null": "hk_customer IS NOT NULL",
        "fk_restaurant_not_null": "hk_restaurant IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def lnk_order():
    return (
        spark.read.table("restaurant_chain_db.staging.stg_orders")
        .select(
            "hk_lnk_order", "hk_order", "hk_customer", "hk_restaurant",
            "load_dts", "record_source",
        )
        .groupBy("hk_lnk_order", "hk_order", "hk_customer", "hk_restaurant")
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
    )
