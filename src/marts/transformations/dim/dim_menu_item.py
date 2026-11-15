"""Marts · Dimension — Menu Item

Current-state menu item dimension from pit_menu_item + hub_menu_item.
No ghost record needed — menu items are always resolved via bridge_order_item
which uses INNER JOINs.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.dim_menu_item",
    comment="Menu item dimension — current state from PIT (price, category, attributes)",
    table_properties={"quality": "marts"},
)
@dp.expect_or_fail("pk_not_null", "restaurant_id IS NOT NULL AND item_id IS NOT NULL")
@dp.expect("valid_price", "price > 0")
def dim_menu_item():
    pit = spark.read.table("restaurant_chain_db.business_vault.pit_menu_item")
    hub = spark.read.table("restaurant_chain_db.raw_vault.hub_menu_item").select(
        "hk_menu_item", "restaurant_id", "item_id"
    )

    return (
        pit.filter("effective_to IS NULL")
        .join(hub, "hk_menu_item", "inner")
        .select(
            "restaurant_id",
            "item_id",
            "name",
            "category",
            "price",
            "ingredients",
            "is_vegetarian",
            "spice_level",
        )
    )
