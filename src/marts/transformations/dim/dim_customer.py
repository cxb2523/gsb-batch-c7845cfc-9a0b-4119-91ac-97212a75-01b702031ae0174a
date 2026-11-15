"""Marts · Dimension — Customer (with Ghost Record)

Current-state customer dimension from pit_customer + hub_customer.
Includes an "UNKNOWN" ghost record for late-arriving data (DV 2.1
best practice — reviews referencing orders not yet in the vault).
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.dim_customer",
    comment="Customer dimension — current state from PIT + ghost record for unknowns",
    table_properties={
        "quality": "marts",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["customer_city"],
)
@dp.expect_or_fail("pk_not_null", "customer_id IS NOT NULL")
@dp.expect("city_not_null", "customer_city IS NOT NULL")
def dim_customer():
    pit = spark.read.table("restaurant_chain_db.business_vault.pit_customer")
    hub = spark.read.table("restaurant_chain_db.raw_vault.hub_customer").select("hk_customer", "customer_id")

    # Current-state: latest version per customer (effective_to IS NULL)
    current = (
        pit.filter("effective_to IS NULL")
        .join(hub, "hk_customer", "inner")
        .select(
            "customer_id",
            "name",
            "email",
            "phone",
            F.col("city").alias("customer_city"),
            "join_date",
        )
    )

    # Ghost record for late-arriving / orphan data
    ghost = spark.sql("""
        SELECT
            'UNKNOWN'   AS customer_id,
            'Unknown'   AS name,
            'Unknown'   AS email,
            'Unknown'   AS phone,
            'Unknown'   AS customer_city,
            CAST(NULL AS DATE) AS join_date
    """)

    return current.unionByName(ghost)
