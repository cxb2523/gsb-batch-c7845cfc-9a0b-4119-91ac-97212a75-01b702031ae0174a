"""Business Vault · PIT — Menu Item (Lean SCD2-Style)

Critical for price-over-time analysis. Each price change in
sat_menu_item creates a new version with distinct effective dates.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F
from pyspark.sql.window import Window

CATALOG_SCHEMA = "restaurant_chain_db.business_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.pit_menu_item",
    comment="Lean PIT — menu item attribute versions (price tracking)",
    table_properties={
        "quality": "business_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_menu_item"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_menu_item IS NOT NULL",
        "effective_from_not_null": "effective_from IS NOT NULL",
    }
)
@dp.expect("valid_price", "price > 0")
def pit_menu_item():
    w = Window.partitionBy("hk_menu_item").orderBy("load_dts")

    return (
        spark.read.table("restaurant_chain_db.raw_vault.sat_menu_item")
        .select(
            "hk_menu_item",
            F.col("load_dts").alias("effective_from"),
            F.lead("load_dts").over(w).alias("effective_to"),
            "name",
            "category",
            "price",
            "ingredients",
            "is_vegetarian",
            "spice_level",
            "record_source",
        )
    )
