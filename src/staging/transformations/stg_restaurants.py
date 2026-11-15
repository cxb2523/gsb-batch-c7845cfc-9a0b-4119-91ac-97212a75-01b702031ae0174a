"""Staging · Restaurants

Reads the bronze restaurants MV and prepares it for the raw vault
with hash key and hash-diff computation.

Downstream consumers: hub_restaurant, sat_restaurant
"""
from pyspark import pipelines as dp

from _dv_hash import DV_LOAD_DTS, dv_record_source, hash_diff, hash_key

CATALOG_SCHEMA = "restaurant_chain_db.staging"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.stg_restaurants",
    comment="Staged restaurants with DV hash key, hash-diff, and audit metadata",
    table_properties={"quality": "staging"},
)
@dp.expect_or_fail("valid_business_key", "restaurant_id IS NOT NULL")
def stg_restaurants():
    df = spark.read.table("restaurant_chain_db.01_bronze.restaurants")

    df = hash_key(df, ["restaurant_id"], "hk_restaurant")
    df = hash_diff(
        df,
        ["name", "city", "country", "address", "opening_date", "phone"],
    )

    df = df.withColumn("load_dts", DV_LOAD_DTS())
    df = df.withColumn("record_source", dv_record_source("BRONZE_RESTAURANTS"))

    return df.select(
        "hk_restaurant", "hash_diff",
        "restaurant_id",
        "name", "city", "country", "address", "opening_date", "phone",
        "load_dts", "record_source",
    )
