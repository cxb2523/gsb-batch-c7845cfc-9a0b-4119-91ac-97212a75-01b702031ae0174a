"""Staging · Menu Items

Reads the bronze menu_items MV and prepares it for the raw vault.
Menu items have a composite business key (restaurant_id, item_id)
because item_id is scoped per restaurant.

Downstream consumers: hub_menu_item, sat_menu_item
"""
from pyspark import pipelines as dp

from _dv_hash import DV_LOAD_DTS, dv_record_source, hash_diff, hash_key

CATALOG_SCHEMA = "restaurant_chain_db.staging"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.stg_menu_items",
    comment="Staged menu items with DV hash key (composite), hash-diff, and audit metadata",
    table_properties={"quality": "staging"},
)
@dp.expect_all_or_fail(
    {
        "valid_restaurant_id": "restaurant_id IS NOT NULL",
        "valid_item_id": "item_id IS NOT NULL",
    }
)
def stg_menu_items():
    df = spark.read.table("restaurant_chain_db.01_bronze.menu_items")

    # Composite business key: (restaurant_id, item_id)
    df = hash_key(df, ["restaurant_id", "item_id"], "hk_menu_item")
    df = hash_diff(
        df,
        [
            "name", "category", "price",
            "ingredients", "is_vegetarian", "spice_level",
        ],
    )

    df = df.withColumn("load_dts", DV_LOAD_DTS())
    df = df.withColumn("record_source", dv_record_source("BRONZE_MENU_ITEMS"))

    return df.select(
        "hk_menu_item", "hash_diff",
        "restaurant_id", "item_id",
        "name", "category", "price",
        "ingredients", "is_vegetarian", "spice_level",
        "load_dts", "record_source",
    )
