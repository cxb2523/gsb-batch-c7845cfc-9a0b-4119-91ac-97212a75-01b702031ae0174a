"""Bronze · Parsed EventHub Orders (Temporary View)

Pipeline-private view that parses EventHub JSON without persisting to
storage. Feeds into 01_bronze.orders via append flow.

Cost: Zero storage — implemented as a temporary view. The parsed data
is consumed immediately by the bronze orders append flow.
"""
from pyspark import pipelines as dp
from pyspark.sql.functions import col, from_json
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)

ITEM_SCHEMA = StructType([
    StructField("item_id",    StringType()),
    StructField("name",       StringType()),
    StructField("category",   StringType()),
    StructField("quantity",   IntegerType()),
    StructField("unit_price", DoubleType()),
    StructField("subtotal",   DoubleType()),
])

ORDER_SCHEMA = StructType([
    StructField("order_id",       StringType()),
    StructField("timestamp",      StringType()),
    StructField("restaurant_id",  StringType()),
    StructField("customer_id",    StringType()),
    StructField("order_type",     StringType()),
    StructField("items",          ArrayType(ITEM_SCHEMA)),
    StructField("total_amount",   DoubleType()),
    StructField("payment_method", StringType()),
    StructField("order_status",   StringType()),
    StructField("created_at",     StringType()),
])


@dp.temporary_view()
@dp.expect("valid_parse", "order_id IS NOT NULL")
def eventhub_parsed():
    return (
        spark.readStream
        .table("restaurant_chain_db.00_landing.eventhub_raw")
        .withColumn("parsed", from_json(col("value"), ORDER_SCHEMA))
        .select("parsed.*", "event_time", "ingestion_time")
    )
