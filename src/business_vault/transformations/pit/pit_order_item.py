"""Business Vault · PIT — Order Item (Lean SCD2-Style, Link-Satellite)

PIT on sat_order_item (link-satellite). Tracks line-item payload versions.
Parent key is hk_lnk_order_item (link hash key, not hub hash key).
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F
from pyspark.sql.window import Window

CATALOG_SCHEMA = "restaurant_chain_db.business_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.pit_order_item",
    comment="Lean PIT — order line-item attribute versions (link-satellite)",
    table_properties={
        "quality": "business_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_lnk_order_item"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_lnk_order_item IS NOT NULL",
        "effective_from_not_null": "effective_from IS NOT NULL",
    }
)
@dp.expect("valid_quantity", "quantity > 0 AND unit_price >= 0")
def pit_order_item():
    w = Window.partitionBy("hk_lnk_order_item").orderBy("load_dts")

    return (
        spark.read.table("restaurant_chain_db.raw_vault.sat_order_item")
        .select(
            "hk_lnk_order_item",
            F.col("load_dts").alias("effective_from"),
            F.lead("load_dts").over(w).alias("effective_to"),
            "line_number",
            "item_name",
            "item_category",
            "quantity",
            "unit_price",
            "subtotal",
            "record_source",
        )
    )
