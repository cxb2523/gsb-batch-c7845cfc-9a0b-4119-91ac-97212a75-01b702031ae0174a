"""Business Vault · PIT — Review (Lean SCD2-Style)

One row per review satellite version with effective_from / effective_to.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F
from pyspark.sql.window import Window

CATALOG_SCHEMA = "restaurant_chain_db.business_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.pit_review",
    comment="Lean PIT — review attribute versions with effective_from/to dating",
    table_properties={
        "quality": "business_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_review"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_review IS NOT NULL",
        "effective_from_not_null": "effective_from IS NOT NULL",
    }
)
@dp.expect("valid_rating", "rating BETWEEN 1 AND 5")
def pit_review():
    w = Window.partitionBy("hk_review").orderBy("load_dts")

    return (
        spark.read.table("restaurant_chain_db.raw_vault.sat_review")
        .select(
            "hk_review",
            F.col("load_dts").alias("effective_from"),
            F.lead("load_dts").over(w).alias("effective_to"),
            "review_text",
            "rating",
            "review_timestamp",
            "record_source",
        )
    )
