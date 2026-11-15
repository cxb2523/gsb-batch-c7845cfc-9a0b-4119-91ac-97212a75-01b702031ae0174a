"""Business Vault · Reference — Record Source Registry (DV 2.1)

Centralised governance lookup for all record_source codes used across
the Data Vault. Maps each source system identifier to:
  - Human-readable description
  - Data owner / responsible team
  - Expected SLA (freshness target)
  - Connection type and sensitivity classification

DV 2.1 best practice: every raw vault table carries a record_source
column. This reference table makes those codes queryable and auditable
without tribal knowledge.

Grain: one row per record_source code.
"""
from pyspark import pipelines as dp
from pyspark.sql.types import StructType, StructField, StringType

CATALOG_SCHEMA = "restaurant_chain_db.business_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.ref_record_source",
    comment="Reference — record source registry mapping source codes to owners, SLAs, and descriptions",
    table_properties={
        "quality": "business_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
)
def ref_record_source():
    schema = StructType([
        StructField("record_source", StringType(), False),
        StructField("source_description", StringType(), False),
        StructField("source_system", StringType(), False),
        StructField("connection_type", StringType(), False),
        StructField("data_owner", StringType(), False),
        StructField("data_classification", StringType(), False),
        StructField("expected_sla", StringType(), False),
        StructField("notes", StringType(), True),
    ])

    data = [
        ("BRONZE_ORDERS", "Unified orders from Event Hub + SQL Server",
         "Azure Event Hub + Azure SQL Server", "Kafka streaming + JDBC batch",
         "Data Engineering", "internal",
         "< 15 min (streaming) / daily (historical)",
         "Merged from eventhub_raw (live) and historical_orders (backfill)"),

        ("BRONZE_ORDER_ITEMS", "Order line items extracted from orders JSON",
         "Azure Event Hub + Azure SQL Server", "Derived from BRONZE_ORDERS",
         "Data Engineering", "internal",
         "Same as BRONZE_ORDERS",
         "Exploded from orders.items array in staging"),

        ("BRONZE_CUSTOMERS", "Customer reference data from SQL Server",
         "Azure SQL Server", "JDBC batch",
         "Data Engineering", "confidential",
         "Daily full refresh",
         "PII masked at bronze layer (SHA-256 on name, email, phone)"),

        ("BRONZE_RESTAURANTS", "Restaurant reference data from SQL Server",
         "Azure SQL Server", "JDBC batch",
         "Data Engineering", "internal",
         "Daily full refresh",
         None),

        ("BRONZE_MENU_ITEMS", "Menu item catalog from SQL Server",
         "Azure SQL Server", "JDBC batch",
         "Data Engineering", "internal",
         "Daily full refresh",
         "Restaurant-scoped: same item_id at different restaurants = different entities"),

        ("BRONZE_REVIEWS", "Customer reviews from SQL Server",
         "Azure SQL Server", "JDBC batch",
         "Data Engineering", "internal",
         "Daily full refresh",
         "AI sentiment analysis applied in business vault computed satellite"),
    ]

    return spark.createDataFrame(data, schema)
