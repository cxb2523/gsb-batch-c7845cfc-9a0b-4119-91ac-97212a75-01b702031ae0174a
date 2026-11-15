"""Marts · Aggregate — Review Insights Monthly

Equivalent to gold_serving_analytics.review_insights_monthly.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.agg_review_insights_monthly",
    comment="Monthly review sentiment insights per restaurant — quality and operations teams",
    table_properties={"quality": "marts"},
)
@dp.expect("valid_reviews", "total_reviews > 0")
@dp.expect("valid_avg_rating", "avg_rating BETWEEN 1 AND 5")
def agg_review_insights_monthly():
    return (
        spark.read.table(f"{CATALOG_SCHEMA}.fact_review")
        .groupBy(
            "restaurant_id",
            "restaurant_name",
            "restaurant_city",
            "review_year",
            "review_month",
        )
        .agg(
            F.count("*").alias("total_reviews"),
            F.round(F.avg("rating"), 2).alias("avg_rating"),
            F.round(F.avg(F.when(F.col("sentiment") == "positive", 1).otherwise(0)) * 100, 1).alias("positive_pct"),
            F.round(F.avg(F.when(F.col("sentiment") == "neutral", 1).otherwise(0)) * 100, 1).alias("neutral_pct"),
            F.round(F.avg(F.when(F.col("sentiment") == "negative", 1).otherwise(0)) * 100, 1).alias("negative_pct"),
            F.round(F.avg(F.when(F.col("issue_delivery") == "true", 1).otherwise(0)), 3).alias("issue_delivery_rate"),
            F.round(F.avg(F.when(F.col("issue_food_quality") == "true", 1).otherwise(0)), 3).alias("issue_food_quality_rate"),
            F.round(F.avg(F.when(F.col("issue_pricing") == "true", 1).otherwise(0)), 3).alias("issue_pricing_rate"),
            F.round(F.avg(F.when(F.col("issue_portion_size") == "true", 1).otherwise(0)), 3).alias("issue_portion_size_rate"),
        )
    )
