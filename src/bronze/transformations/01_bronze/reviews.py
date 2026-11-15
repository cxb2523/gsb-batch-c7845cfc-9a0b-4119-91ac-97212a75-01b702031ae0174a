"""Bronze · Reviews

Cleansed reviews dimension snapshot from Azure SQL Server with data
quality enforcement. Note: review_text may contain PII — NLP-based
redaction is recommended for downstream consumption.
"""
from pyspark import pipelines as dp

from read_sql_server import read_sql_server_table


@dp.materialized_view(
    name="restaurant_chain_db.01_bronze.reviews",
    comment="Cleansed reviews — review_text may contain PII (NLP redaction recommended)",
    table_properties={"quality": "bronze"},
)
@dp.expect_or_fail("valid_review_id", "review_id IS NOT NULL")
@dp.expect("valid_rating", "rating BETWEEN 1 AND 5")
def reviews():
    return read_sql_server_table("reviews", dbutils)
