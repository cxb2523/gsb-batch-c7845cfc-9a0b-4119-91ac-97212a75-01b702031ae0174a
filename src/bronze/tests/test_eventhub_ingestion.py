"""End-to-end streaming tests for rejection routing and order dedup.

The runner below emulates the Lakeflow SDP graphs declared in the
transformation files, using the *same* shared logic:

    InMemoryEventHubSource
        -> normalize landing rows          (eventhub_sources.normalize_raw,
                                             identical to the eventhub_raw ST)
        -> split_parsed_orders             (eventhub_parsed temp views)
           ├─ live_orders + watermark dedup
           │     -> foreachBatch Delta MERGE  (emulates orders merge_flow)
           └─ rejection_records
                 -> append sink              (emulates order_rejections flow)

Three message classes are exercised with real micro-batches (one input file
per micro-batch, ``availableNow`` trigger): valid, malformed / missing-id, and
duplicated deliveries.
"""
import os

import pytest

from eventhub_sources import (
    CONF_SOURCE_KIND,
    CONF_SOURCE_PATH,
    InMemoryEventHubSource,
    LocalFileEventHubSource,
    build_eventhub_source,
    normalize_raw,
)
from order_ingest import (
    REASON_MALFORMED_JSON,
    REASON_MISSING_ORDER_ID,
    live_orders,
    merge_orders_batch,
    rejection_records,
    split_parsed_orders,
)

ORDERS_TABLE = "orders"
REJECTIONS_TABLE = "order_rejections"


# ── Test data ─────────────────────────────────────────────────────
def valid_order(order_id, total=42.5):
    return {
        "order_id": order_id,
        "timestamp": "2026-04-09T10:00:00.000Z",
        "restaurant_id": "R-1",
        "customer_id": "C-1",
        "order_type": "delivery",
        "items": [
            {
                "item_id": "I-1",
                "name": "Burger",
                "category": "mains",
                "quantity": 1,
                "unit_price": total,
                "subtotal": total,
            }
        ],
        "total_amount": total,
        "payment_method": "card",
        "order_status": "placed",
        "created_at": "2026-04-09T10:00:00.000Z",
    }


def message(payload, ts="2026-04-09T10:00:00.000Z"):
    return {"timestamp": ts, "value": payload}


# ── Local equivalent of the SDP streaming graph ───────────────────
def _create_target_tables(spark):
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {ORDERS_TABLE} (
          order_id STRING,
          order_timestamp TIMESTAMP,
          restaurant_id STRING,
          customer_id STRING,
          order_type STRING,
          items ARRAY<STRUCT<
            item_id: STRING, name: STRING, category: STRING,
            quantity: INT, unit_price: DOUBLE, subtotal: DOUBLE>>,
          total_amount DOUBLE,
          payment_method STRING,
          order_status STRING,
          source STRING,
          loaded_at TIMESTAMP
        )
        USING DELTA
        """
    )
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {REJECTIONS_TABLE} (
          raw_value STRING,
          event_time TIMESTAMP,
          ingestion_time TIMESTAMP,
          rejection_reason STRING,
          rejected_at TIMESTAMP
        )
        USING DELTA
        """
    )


def run_pipeline_once(env):
    """Start both streaming queries and process all currently available files."""
    spark = env["spark"]
    _create_target_tables(spark)

    raw = normalize_raw(env["source"].read_stream(spark))
    good, rejected = split_parsed_orders(raw)

    orders_stream = live_orders(good)
    rejections_stream = rejection_records(rejected)

    orders_query = (
        orders_stream.writeStream.foreachBatch(
            lambda batch, batch_id: merge_orders_batch(
                batch, batch_id, ORDERS_TABLE
            )
        )
        .option("checkpointLocation", os.path.join(env["checkpoint_dir"], "orders"))
        .trigger(availableNow=True)
        .start()
    )
    rejections_query = (
        rejections_stream.writeStream.format("delta")
        .outputMode("append")
        .option(
            "checkpointLocation",
            os.path.join(env["checkpoint_dir"], "rejections"),
        )
        .trigger(availableNow=True)
        .toTable(REJECTIONS_TABLE)
    )
    orders_query.awaitTermination()
    rejections_query.awaitTermination()


def _ids(rows, column="order_id"):
    return sorted(row[column] for row in rows)


# ── Tests ─────────────────────────────────────────────────────────
def test_valid_bad_and_missing_id_routing(env):
    """One micro-batch with all three message classes routes correctly."""
    env["source"].add_batch([
        message(valid_order("O-100")),                       # good
        message("{not valid json"),                         # malformed JSON
        message(valid_order("O-101")),                      # good
        message(valid_order("O-102", total=9.0)),           # good
        message('{"restaurant_id": "R-9"}'),                # missing order_id
        message('{"order_id": "  ", "restaurant_id": "R-9"}'),  # blank id
    ])

    run_pipeline_once(env)
    spark = env["spark"]

    orders = spark.sql(f"SELECT * FROM {ORDERS_TABLE}").collect()
    assert _ids(orders) == ["O-100", "O-101", "O-102"]
    # Live rows are tagged like the production merge flow.
    assert {row["source"] for row in orders} == {"live"}

    rejected = spark.sql(
        f"SELECT * FROM {REJECTIONS_TABLE} ORDER BY rejection_reason, raw_value"
    ).collect()
    assert len(rejected) == 3
    reasons = sorted(row["rejection_reason"] for row in rejected)
    assert reasons == [
        REASON_MALFORMED_JSON,
        REASON_MISSING_ORDER_ID,
        REASON_MISSING_ORDER_ID,
    ]
    # Required audit columns are populated, including the original raw payload.
    for row in rejected:
        assert row["raw_value"] is not None and row["raw_value"] != ""
        assert row["event_time"] is not None
        assert row["ingestion_time"] is not None
    malformed = [
        row for row in rejected if row["rejection_reason"] == REASON_MALFORMED_JSON
    ][0]
    assert malformed["raw_value"] == "{not valid json"


def test_duplicate_delivery_absorbed_incrementally(env):
    """Same order_id redelivered across micro-batches leaves exactly one row."""
    # Micro-batch 1: two distinct orders, one duplicated inside the batch.
    env["source"].add_batch([
        message(valid_order("O-200", total=10.0)),
        message(valid_order("O-201", total=20.0)),
        message(valid_order("O-200", total=10.0)),  # intra-batch duplicate
    ])
    run_pipeline_once(env)

    spark = env["spark"]
    assert _ids(spark.sql(f"SELECT order_id FROM {ORDERS_TABLE}").collect()) == [
        "O-200",
        "O-201",
    ]
    assert spark.sql(
        f"SELECT COUNT(*) AS c FROM {ORDERS_TABLE} WHERE order_id = 'O-200'"
    ).collect()[0]["c"] == 1

    # Micro-batch 2: Event Hub redelivers O-200 (at-least-once) plus a new O-202.
    env["source"].add_batch([
        message(valid_order("O-200", total=10.0)),
        message(valid_order("O-202", total=30.0)),
    ])
    run_pipeline_once(env)

    final_rows = spark.sql(
        f"SELECT order_id, total_amount FROM {ORDERS_TABLE}"
    ).collect()
    assert _ids(final_rows) == ["O-200", "O-201", "O-202"]
    # Still exactly one physical row for the redelivered order.
    counts = {
        row["order_id"]: row["c"]
        for row in spark.sql(
            f"SELECT order_id, COUNT(*) AS c "
            f"FROM {ORDERS_TABLE} GROUP BY order_id"
        ).collect()
    }
    assert counts == {"O-200": 1, "O-201": 1, "O-202": 1}
    # No rejections were produced for good/duplicate messages.
    assert spark.sql(
        f"SELECT COUNT(*) AS c FROM {REJECTIONS_TABLE}"
    ).collect()[0]["c"] == 0


def test_bad_messages_never_reach_orders_over_multiple_batches(env):
    """Rejection and dedup hold when bad and duplicate messages interleave."""
    env["source"].add_batch([
        message(valid_order("O-300")),
        message("garbage-1"),
    ])
    env["source"].add_batch([
        message(valid_order("O-300")),       # duplicate of batch 1
        message('{"order_id": null}'),       # null order_id
        message(valid_order("O-301")),
    ])
    run_pipeline_once(env)

    spark = env["spark"]
    assert _ids(spark.sql(f"SELECT order_id FROM {ORDERS_TABLE}").collect()) == [
        "O-300",
        "O-301",
    ]
    rejected = spark.sql(
        f"SELECT rejection_reason, COUNT(*) AS c "
        f"FROM {REJECTIONS_TABLE} GROUP BY rejection_reason"
    ).collect()
    assert {row["rejection_reason"]: row["c"] for row in rejected} == {
        REASON_MALFORMED_JSON: 1,
        REASON_MISSING_ORDER_ID: 1,
    }


def test_injection_entry_selects_local_source_without_secrets(env, tmp_path):
    """The documented Spark-conf injection switches source implementations."""
    spark = env["spark"]
    input_dir = str(tmp_path / "injected")
    os.makedirs(input_dir, exist_ok=True)

    spark.conf.set(CONF_SOURCE_KIND, "local-file")
    spark.conf.set(CONF_SOURCE_PATH, input_dir)
    source = build_eventhub_source(spark, dbutils=None)
    assert isinstance(source, LocalFileEventHubSource)
    assert source.path == input_dir

    # A streaming DataFrame is produced without any Kafka/secret access.
    stream = source.read_stream(spark)
    assert stream.isStreaming
    assert {"value", "event_time", "ingestion_time"}.issubset(stream.columns)

    # Direct in-memory injection (used by the other tests) is also supported.
    direct = InMemoryEventHubSource(str(tmp_path / "mem"), spark)
    assert build_eventhub_source(spark, source=direct) is direct

    spark.conf.unset(CONF_SOURCE_KIND)
    spark.conf.unset(CONF_SOURCE_PATH)


def test_kafka_source_requires_explicit_runtime_dependencies(env):
    """Production source must never be constructed accidentally in local tests."""
    spark = env["spark"]
    spark.conf.set(CONF_SOURCE_KIND, "kafka")
    with pytest.raises(ValueError, match="dbutils is required"):
        build_eventhub_source(spark, dbutils=None)
    spark.conf.unset(CONF_SOURCE_KIND)


def test_transformation_modules_declare_rejection_and_merge_flows():
    """The actual SDP transformation files wire the new datasets/flows."""
    import importlib

    from conftest import REGISTERED

    # Importing the modules registers their decorators through the SDP stub.
    import eventhub_parsed as parsed_module  # noqa: F401
    importlib.import_module("order_rejections")
    importlib.import_module("orders")

    assert "eventhub_parsed" in REGISTERED
    assert "eventhub_rejected" in REGISTERED
    assert REGISTERED["order_rejections_from_eventhub"]["kind"] == "append_flow"
    assert REGISTERED["orders_from_eventhub"]["kind"] == "merge_flow"
    # The historical once-flow is preserved verbatim.
    historical = REGISTERED["orders_from_historical"]
    assert historical["kind"] == "append_flow"
    assert historical["kwargs"].get("once") is True
