"""Marts · Dimension — Date

Calendar dimension sourced from business_vault.ref_date_dim.
Adds an integer date_key (YYYYMMDD) for traditional star-schema joins.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.dim_date",
    comment="Calendar date dimension with integer date_key (YYYYMMDD)",
    table_properties={"quality": "marts"},
)
@dp.expect_or_fail("pk_not_null", "date_key IS NOT NULL AND date_value IS NOT NULL")
def dim_date():
    return (
        spark.read.table("restaurant_chain_db.business_vault.ref_date_dim")
        .select(
            (F.year("as_of_date") * 10000 + F.month("as_of_date") * 100 + F.dayofmonth("as_of_date"))
            .cast("int").alias("date_key"),
            F.col("as_of_date").alias("date_value"),
            "year",
            "quarter",
            "month",
            "day_of_month",
            "day_of_week",
            "day_name",
            "is_weekend",
            "week_of_year",
            "month_name",
        )
    )
