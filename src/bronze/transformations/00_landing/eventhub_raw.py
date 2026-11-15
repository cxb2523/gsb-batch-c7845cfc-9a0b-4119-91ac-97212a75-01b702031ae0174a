"""Landing · EventHub Raw Stream

Raw Event Hubs messages ingested via Kafka-compatible connector.
Published to 00_landing as a streaming table. Data is kept as-is —
JSON parsing happens downstream in the bronze temp view.

Production settings:
  failOnDataLoss=true   → Fail fast if offsets expire (never silently skip).
  maxOffsetsPerTrigger  → Cap batch size to prevent backlog spikes on restart.
  kafka.request/session → Resilience against transient EventHub delays.
"""
from pyspark import pipelines as dp
from pyspark.sql.functions import col, current_timestamp

# ── Event Hubs / Kafka Configuration ─────────────────────────────
EH_NAMESPACE = spark.conf.get("EH_NAMESPACE")
EH_NAME      = spark.conf.get("EH_NAME")
EH_CONN_STR  = dbutils.secrets.get(scope="res-chain-pipeline", key="eh-conn-str")

KAFKA_OPTIONS = {
    "kafka.bootstrap.servers"  : f"{EH_NAMESPACE}.servicebus.windows.net:9093",
    "subscribe"                : EH_NAME,
    "kafka.sasl.mechanism"     : "PLAIN",
    "kafka.security.protocol"  : "SASL_SSL",
    "kafka.sasl.jaas.config"   : (
        "kafkashaded.org.apache.kafka.common.security.plain.PlainLoginModule"
        f' required username="$ConnectionString" password="{EH_CONN_STR}";'
    ),
    "startingOffsets"          : spark.conf.get("startingOffsets"),
    "maxOffsetsPerTrigger"     : spark.conf.get("maxOffsetsPerTrigger"),
    "failOnDataLoss"           : "true",
    "kafka.request.timeout.ms" : "60000",
    "kafka.session.timeout.ms" : "30000",
}


# ── Landing ST ────────────────────────────────────────────────────

@dp.table(
    name="restaurant_chain_db.00_landing.eventhub_raw",
    comment="Raw Event Hubs messages ingested via Kafka connector",
    table_properties={
        "quality": "landing",
        "pipelines.reset.allowed": "false",
    },
)
@dp.expect("valid_topic", "topic IS NOT NULL")
@dp.expect("valid_value", "value IS NOT NULL")
@dp.expect("valid_event_time", "event_time IS NOT NULL")
@dp.expect("valid_ingestion_time", "ingestion_time IS NOT NULL")
def eventhub_raw():
    return (
        spark.readStream
        .format("kafka")
        .options(**KAFKA_OPTIONS)
        .load()
        .withColumn("value", col("value").cast("string"))
        .withColumn("event_time", col("timestamp"))
        .withColumn("ingestion_time", current_timestamp())
    )
