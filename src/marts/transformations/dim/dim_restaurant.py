"""Marts · Dimension — Restaurant (with Ghost Record)

Current-state restaurant dimension from pit_restaurant + hub_restaurant.
Includes an "UNKNOWN" ghost record for late-arriving data.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.dim_restaurant",
    comment="Restaurant dimension — current state from PIT + ghost record for unknowns",
    table_properties={"quality": "marts"},
)
@dp.expect_or_fail("pk_not_null", "restaurant_id IS NOT NULL")
@dp.expect("name_not_null", "restaurant_name IS NOT NULL")
def dim_restaurant():
    pit = spark.read.table("restaurant_chain_db.business_vault.pit_restaurant")
    hub = spark.read.table("restaurant_chain_db.raw_vault.hub_restaurant").select("hk_restaurant", "restaurant_id")

    current = (
        pit.filter("effective_to IS NULL")
        .join(hub, "hk_restaurant", "inner")
        .select(
            "restaurant_id",
            F.col("name").alias("restaurant_name"),
            F.col("city").alias("restaurant_city"),
            "country",
            "address",
            "opening_date",
            "phone",
        )
    )

    ghost = spark.sql("""
        SELECT
            'UNKNOWN'   AS restaurant_id,
            'Unknown'   AS restaurant_name,
            'Unknown'   AS restaurant_city,
            'Unknown'   AS country,
            'Unknown'   AS address,
            CAST(NULL AS DATE) AS opening_date,
            'Unknown'   AS phone
    """)

    return current.unionByName(ghost)
