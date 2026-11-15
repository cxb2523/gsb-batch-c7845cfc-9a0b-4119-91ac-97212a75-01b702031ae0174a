"""Business Vault · PIT — Order (Lean SCD2-Style)

Tracks order header state versions (status changes, amount corrections).
0.6% of orders currently have multiple versions.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F
from pyspark.sql.window import Window

CATALOG_SCHEMA = "restaurant_chain_db.business_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.pit_order",
    comment="Lean PIT — order header attribute versions",
    table_properties={
        "quality": "business_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_order"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_order IS NOT NULL",
        "effective_from_not_null": "effective_from IS NOT NULL",
    }
)
@dp.expect("valid_total_amount", "total_amount >= 0")
@dp.expect("valid_order_type", "order_type IN ('dine_in', 'takeaway', 'delivery')")
def pit_order():
    w = Window.partitionBy("hk_order").orderBy("load_dts")

    return (
        spark.read.table("restaurant_chain_db.raw_vault.sat_order_header")
        .select(
            "hk_order",
            F.col("load_dts").alias("effective_from"),
            F.lead("load_dts").over(w).alias("effective_to"),
            "order_timestamp",
            "order_type",
            "total_amount",
            "payment_method",
            "order_status",
            "record_source",
        )
    )
