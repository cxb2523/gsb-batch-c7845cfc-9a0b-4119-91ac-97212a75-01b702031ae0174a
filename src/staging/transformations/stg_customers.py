"""Staging · Customers

Reads the PII-masked bronze customers MV and prepares it for the
raw vault with hash key and hash-diff computation.

Note: name, email, phone are already SHA-256 hashed in bronze for
PII protection.  The hash-diff is computed over the hashed values,
which is correct — a change in the underlying PII still produces a
different SHA-256 output, so the hash-diff detects the change.

Downstream consumers: hub_customer, sat_customer
"""
from pyspark import pipelines as dp

from _dv_hash import DV_LOAD_DTS, dv_record_source, hash_diff, hash_key

CATALOG_SCHEMA = "restaurant_chain_db.staging"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.stg_customers",
    comment="Staged customers with DV hash key, hash-diff, and audit metadata",
    table_properties={"quality": "staging"},
)
@dp.expect_or_fail("valid_business_key", "customer_id IS NOT NULL")
def stg_customers():
    df = spark.read.table("restaurant_chain_db.01_bronze.customers")

    df = hash_key(df, ["customer_id"], "hk_customer")
    df = hash_diff(df, ["name", "email", "phone", "city", "join_date"])

    df = df.withColumn("load_dts", DV_LOAD_DTS())
    df = df.withColumn("record_source", dv_record_source("BRONZE_CUSTOMERS"))

    return df.select(
        "hk_customer", "hash_diff",
        "customer_id",
        "name", "email", "phone", "city", "join_date",
        "load_dts", "record_source",
    )
