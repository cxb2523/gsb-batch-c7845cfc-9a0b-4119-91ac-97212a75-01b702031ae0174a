"""Injectable Event Hub input sources.

The ingestion never reads Kafka directly inside its business logic. Instead it
asks :func:`build_eventhub_source` for an *input source* — an object exposing a
single method::

    source.read_stream(spark) -> streaming DataFrame

whose output is normalized to the landing schema (``key``, ``topic``,
``partition``, ``offset``, ``timestamp``, ``value`` + the derived
``event_time`` / ``ingestion_time`` columns are added by the landing layer).

Three implementations are provided:

==============================  ============================================
Source                          Used for
==============================  ============================================
:class:`KafkaEventHubSource`    Production: real Azure Event Hub over the
                                Kafka-compatible connector.
:class:`LocalFileEventHubSource` Local/reproducible runs: reads JSON files
                                (one Kafka-like record per line) from a
                                directory with ``spark.readStream.json``.
:class:`InMemoryEventHubSource` Tests: wraps an in-memory list of records by
                                materialising them to a local scratch dir and
                                reading them as a stream.
==============================  ============================================

Injection entry
---------------
The implementation is chosen by the Spark configuration key
``bronze.eventhub.source`` (settable through the Lakeflow pipeline
*Settings → Configuration*, i.e. ``spark.conf``):

* ``kafka`` (default / absent) — production Event Hub, requires the connection
  string secret.
* ``local-file`` — read from the directory in
  ``bronze.eventhub.source.path``. No connection string or Databricks secret is
  ever touched.
* ``in-memory`` — records are injected programmatically; tests pass the records
  directly to :class:`InMemoryEventHubSource` instead of going through the
  factory.

This is the single seam that makes the whole pipeline testable without a real
Event Hub: the parse / reject / dedup logic only sees a streaming DataFrame and
is identical regardless of which source produced it.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col, current_timestamp
from pyspark.sql.types import (
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# Spark conf keys used for injection.
CONF_SOURCE_KIND = "bronze.eventhub.source"
CONF_SOURCE_PATH = "bronze.eventhub.source.path"

# File-source schema. The JSON file reader would base64-decode string values
# written into BinaryType columns, so text payloads are read as STRING here;
# normalize_raw() converges both sources to the landing column types
# (binary key, string value).
RAW_SCHEMA = StructType([
    StructField("key", StringType(), True),
    StructField("topic", StringType(), True),
    StructField("partition", IntegerType(), True),
    StructField("offset", LongType(), True),
    StructField("timestamp", TimestampType(), True),
    StructField("value", StringType(), True),
])


class EventHubSource:
    """Input-source interface. Every implementation returns a streaming
    DataFrame normalized to the landing shape (``key`` binary, ``topic``,
    ``partition``, ``offset``, ``timestamp``, ``value`` string plus derived
    ``event_time`` / ``ingestion_time``)."""

    kind: str = "abstract"

    def read_stream(self, spark: SparkSession) -> DataFrame:  # pragma: no cover
        raise NotImplementedError


def normalize_raw(df: DataFrame) -> DataFrame:
    """Apply the landing-layer normalization shared by every source.

    Mirrors the original ``eventhub_raw`` transformation:
      * ``key``   -> binary (Kafka native type; harmless for null file keys)
      * ``value`` -> string (JSON payload; binary Kafka bytes are UTF-8 decoded)
      * ``event_time``      = the broker record timestamp
      * ``ingestion_time``  = current processing time
    """
    return (
        df.select("key", "topic", "partition", "offset", "timestamp", "value")
        .withColumn("key", col("key").cast("binary"))
        .withColumn("value", col("value").cast("string"))
        .withColumn("event_time", col("timestamp"))
        .withColumn("ingestion_time", current_timestamp())
    )


class KafkaEventHubSource(EventHubSource):
    """Production source: Azure Event Hub through the Kafka connector.

    Connection options are built lazily so that merely constructing the source
    (e.g. during collection/import) never requires the secret.
    """

    kind = "kafka"

    def __init__(
        self,
        options: Dict[str, str],
        namespace: str,
        event_hub_name: str,
        connection_string: str,
        starting_offsets: str,
        max_offsets_per_trigger: str,
    ):
        self._options = dict(options)
        self._namespace = namespace
        self._event_hub_name = event_hub_name
        self._connection_string = connection_string
        self._starting_offsets = starting_offsets
        self._max_offsets_per_trigger = max_offsets_per_trigger

    @classmethod
    def from_conf(cls, spark: SparkSession, dbutils: Any) -> "KafkaEventHubSource":
        """Build the production source from pipeline ``spark.conf`` + secrets."""
        namespace = spark.conf.get("EH_NAMESPACE")
        event_hub_name = spark.conf.get("EH_NAME")
        connection_string = dbutils.secrets.get(
            scope="res-chain-pipeline", key="eh-conn-str"
        )
        return cls(
            options={},
            namespace=namespace,
            event_hub_name=event_hub_name,
            connection_string=connection_string,
            starting_offsets=spark.conf.get("startingOffsets"),
            max_offsets_per_trigger=spark.conf.get("maxOffsetsPerTrigger"),
        )

    def kafka_options(self) -> Dict[str, str]:
        # Production settings kept identical to the original implementation:
        # failOnDataLoss=true fails fast instead of silently skipping offsets.
        return {
            "kafka.bootstrap.servers": (
                f"{self._namespace}.servicebus.windows.net:9093"
            ),
            "subscribe": self._event_hub_name,
            "kafka.sasl.mechanism": "PLAIN",
            "kafka.security.protocol": "SASL_SSL",
            "kafka.sasl.jaas.config": (
                "kafkashaded.org.apache.kafka.common.security.plain."
                "PlainLoginModule required "
                f'username="$ConnectionString" password="{self._connection_string}";'
            ),
            "startingOffsets": self._starting_offsets,
            "maxOffsetsPerTrigger": self._max_offsets_per_trigger,
            "failOnDataLoss": "true",
            "kafka.request.timeout.ms": "60000",
            "kafka.session.timeout.ms": "30000",
            **self._options,
        }

    def read_stream(self, spark: SparkSession) -> DataFrame:
        return normalize_raw(
            spark.readStream.format("kafka").options(**self.kafka_options()).load()
        )


class LocalFileEventHubSource(EventHubSource):
    """Local source: newline-delimited JSON files in a watched directory.

    Each file line is one Kafka-like record::

        {"topic": "orders", "partition": 0, "offset": 1,
         "timestamp": "2026-04-09T10:00:00.000Z",
         "value": "<order JSON as a string>"}

    ``key``/``partition``/``offset`` are optional. New files dropped into the
    directory appear as subsequent micro-batches, which is exactly what the
    streaming tests use to prove incremental rejection and dedup.
    """

    kind = "local-file"

    def __init__(self, path: str):
        self.path = path

    def read_stream(self, spark: SparkSession) -> DataFrame:
        reader = (
            spark.readStream.schema(RAW_SCHEMA)
            .option("maxFilesPerTrigger", 1)
            .json(self.path)
        )
        return normalize_raw(reader)


class InMemoryEventHubSource(LocalFileEventHubSource):
    """Test source: feed an in-memory list of records as a stream.

    Records are written as JSON files (one file per call = one micro-batch)
    into a scratch directory that :class:`LocalFileEventHubSource` watches.
    A record only needs ``timestamp`` and ``value``; Kafka-only fields default
    to null. No connector, connection string or secret is involved.
    """

    kind = "in-memory"

    def __init__(self, scratch_dir: str, spark: SparkSession):
        super().__init__(scratch_dir)
        self._spark = spark
        self._batch_no = 0
        os.makedirs(scratch_dir, exist_ok=True)

    @staticmethod
    def _encode_value(value: Any) -> str:
        # Accept either an already-serialised payload or a dict to serialise.
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def add_batch(self, records: List[Dict[str, Any]]) -> str:
        """Persist one micro-batch of in-memory records and return its path."""
        self._batch_no += 1
        path = os.path.join(self.path, f"batch-{self._batch_no:04d}.json")
        lines = []
        for record in records:
            payload = {
                "key": record.get("key"),
                "topic": record.get("topic", "orders"),
                "partition": record.get("partition"),
                "offset": record.get("offset"),
                "timestamp": record["timestamp"],
                "value": self._encode_value(record["value"]),
            }
            lines.append(json.dumps(payload, ensure_ascii=False))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        return path


def build_eventhub_source(
    spark: SparkSession,
    dbutils: Optional[Any] = None,
    *,
    source: Optional[EventHubSource] = None,
) -> EventHubSource:
    """Resolve the input source — the pipeline's single injection entry.

    Parameters
    ----------
    spark:
        Active Spark session (used to read ``spark.conf``).
    dbutils:
        Only required for the production Kafka source (secret lookup). Ignored
        by the local/in-memory sources.
    source:
        Explicit override, mainly for tests. When provided it wins over
        configuration.
    """
    if source is not None:
        return source

    kind = spark.conf.get(CONF_SOURCE_KIND, "kafka")
    if kind == "kafka":
        if dbutils is None:
            raise ValueError(
                "dbutils is required for the kafka Event Hub source "
                "(connection-string secret lookup)"
            )
        return KafkaEventHubSource.from_conf(spark, dbutils)
    if kind == "local-file":
        path = spark.conf.get(CONF_SOURCE_PATH)
        if not path:
            raise ValueError(
                f"{CONF_SOURCE_PATH} must point at the input directory "
                "when using the local-file Event Hub source"
            )
        return LocalFileEventHubSource(path)
    raise ValueError(
        f"Unknown {CONF_SOURCE_KIND}={kind!r}; "
        "expected one of: kafka, local-file (or inject an "
        "InMemoryEventHubSource via the source= argument)"
    )
