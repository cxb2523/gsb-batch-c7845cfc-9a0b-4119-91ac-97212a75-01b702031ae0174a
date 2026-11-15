"""Business Vault · Reference — Date Dimension

Calendar date spine generated from the range of order timestamps
observed in the raw vault. Grows incrementally as new order dates arrive.

Used by PIT/bridge consumers and the marts dim_date.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.business_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.ref_date_dim",
    comment="Reference date dimension — calendar spine from order timestamp range",
    table_properties={"quality": "business_vault"},
)
@dp.expect_all_or_fail(
    {
        "date_not_null": "as_of_date IS NOT NULL",
        "year_not_null": "year IS NOT NULL AND month IS NOT NULL",
    }
)
def ref_date_dim():
    # Generate date range from min to max order_timestamp in sat_order_header
    date_range = (
        spark.read.table("restaurant_chain_db.raw_vault.sat_order_header")
        .select(
            F.min(F.to_date("order_timestamp")).alias("min_date"),
            F.max(F.to_date("order_timestamp")).alias("max_date"),
        )
    )

    return (
        date_range
        .select(F.explode(F.sequence("min_date", "max_date", F.expr("INTERVAL 1 DAY"))).alias("as_of_date"))
        .select(
            "as_of_date",
            F.year("as_of_date").alias("year"),
            F.quarter("as_of_date").alias("quarter"),
            F.month("as_of_date").alias("month"),
            F.dayofmonth("as_of_date").alias("day_of_month"),
            F.dayofweek("as_of_date").alias("day_of_week"),
            F.date_format("as_of_date", "EEEE").alias("day_name"),
            F.dayofweek("as_of_date").isin(1, 7).alias("is_weekend"),
            F.weekofyear("as_of_date").alias("week_of_year"),
            F.date_format("as_of_date", "MMMM").alias("month_name"),
        )
    )
