"""Business Vault · PIT — Restaurant (Lean SCD2-Style)

One row per satellite version with effective_from / effective_to.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F
from pyspark.sql.window import Window

CATALOG_SCHEMA = "restaurant_chain_db.business_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.pit_restaurant",
    comment="Lean PIT — restaurant attribute versions with effective_from/to dating",
    table_properties={
        "quality": "business_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_restaurant"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_restaurant IS NOT NULL",
        "effective_from_not_null": "effective_from IS NOT NULL",
    }
)
@dp.expect("valid_date_order", "effective_to IS NULL OR effective_to > effective_from")
def pit_restaurant():
    w = Window.partitionBy("hk_restaurant").orderBy("load_dts")

    return (
        spark.read.table("restaurant_chain_db.raw_vault.sat_restaurant")
        .select(
            "hk_restaurant",
            F.col("load_dts").alias("effective_from"),
            F.lead("load_dts").over(w).alias("effective_to"),
            "name",
            "city",
            "country",
            "address",
            "opening_date",
            "phone",
            "record_source",
        )
    )
