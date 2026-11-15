"""Business Vault · Computed Satellite — Review Sentiment (AI-Enriched)

Streams from reviews_tracked_dv (CDC) so each review is processed
through ai_query exactly once. Same process-once pattern as the
existing 02_silver.fact_reviews.

Process-once economics:
  First run:  84 reviews → 84 ai_query calls
  Later runs: Only new/changed reviews → O(delta) ai_query calls
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.business_vault"


@dp.table(
    name=f"{CATALOG_SCHEMA}.sat_review_sentiment",
    comment="Computed satellite — AI sentiment analysis on review text (process-once via CDC streaming)",
    table_properties={"quality": "business_vault"},
)
@dp.expect_all_or_drop(
    {
        "valid_hk_review": "hk_review IS NOT NULL",
        "valid_sentiment": "sentiment IN ('positive', 'neutral', 'negative')",
    }
)
@dp.expect("issue_delivery_not_null", "issue_delivery IS NOT NULL")
@dp.expect("issue_food_quality_not_null", "issue_food_quality IS NOT NULL")
def sat_review_sentiment():
    return (
        spark.readStream
        .option("readChangeFeed", "true")
        .table("reviews_tracked_dv")
        # Keep only new reviews (insert) and updated review text (update_postimage).
        .filter(F.col("_change_type").isin("insert", "update_postimage"))
        .drop("_change_type", "_commit_version", "_commit_timestamp")
        # --- AI sentiment analysis (expensive — runs only on this micro-batch) ---
        .withColumn(
            "analysis_json",
            F.expr(
                """
                ai_query(
                    'databricks-gpt-oss-20b',
                    CONCAT(
                        'Analyze the following restaurant review. Determine the overall sentiment and identify specific issues. ',
                        'Rules: sentiment must be exactly one of: positive, neutral, negative. ',
                        'Each issue field must be true or false. ',
                        'Each reason field should contain a brief explanation if the issue is true, otherwise empty string. ',
                        'Review: ', review_text
                    ),
                    responseFormat => 'STRUCT<review_analysis:STRUCT<sentiment:STRING, issue_delivery:BOOLEAN, issue_delivery_reason:STRING, issue_food_quality:BOOLEAN, issue_food_quality_reason:STRING, issue_pricing:BOOLEAN, issue_pricing_reason:STRING, issue_portion_size:BOOLEAN, issue_portion_size_reason:STRING>>'
                )
                """
            ),
        )
        # --- Output schema (using get_json_object to match proven pattern from fact_reviews) ---
        .select(
            "hk_review",
            "review_text",
            "rating",
            "review_timestamp",
            "record_source",
            F.get_json_object("analysis_json", "$.sentiment").alias("sentiment"),
            F.get_json_object("analysis_json", "$.issue_delivery").alias("issue_delivery"),
            F.get_json_object("analysis_json", "$.issue_delivery_reason").alias("issue_delivery_reason"),
            F.get_json_object("analysis_json", "$.issue_food_quality").alias("issue_food_quality"),
            F.get_json_object("analysis_json", "$.issue_food_quality_reason").alias("issue_food_quality_reason"),
            F.get_json_object("analysis_json", "$.issue_pricing").alias("issue_pricing"),
            F.get_json_object("analysis_json", "$.issue_pricing_reason").alias("issue_pricing_reason"),
            F.get_json_object("analysis_json", "$.issue_portion_size").alias("issue_portion_size"),
            F.get_json_object("analysis_json", "$.issue_portion_size_reason").alias("issue_portion_size_reason"),
        )
    )
