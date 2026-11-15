"""Bronze · Customers (PII Masked)

PII-masked customer snapshot from Azure SQL Server. Name, email, and
phone are one-way hashed with SHA-256 at ingestion — downstream tables
(including SCD Type 2 in silver) never see plaintext PII.
"""
from pyspark import pipelines as dp
from pyspark.sql.functions import col, sha2

from read_sql_server import read_sql_server_table


@dp.materialized_view(
    name="restaurant_chain_db.01_bronze.customers",
    comment="PII-masked customers — name, email, phone hashed with SHA-256",
    table_properties={"quality": "bronze"},
)
@dp.expect_or_fail("valid_customer_id", "customer_id IS NOT NULL")
def customers():
    return (
        read_sql_server_table("customers", dbutils)
        .withColumn("name",  sha2(col("name"),  256))
        .withColumn("email", sha2(col("email"), 256))
        .withColumn("phone", sha2(col("phone"), 256))
    )
