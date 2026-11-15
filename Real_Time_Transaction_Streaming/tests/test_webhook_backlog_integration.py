"""End-to-end backlog flow against a real PostgreSQL instance.

Runs only when ``BACKLOG_TEST_PG_DSN`` is set, e.g.::

    BACKLOG_TEST_PG_DSN=postgresql://postgres:postgres@localhost:5432/postgres \
        pytest tests/test_webhook_backlog_integration.py

It proves the full contract: a callback whose Kafka publish fails is acked
with 200 and landed in Postgres, then can be listed and replayed exactly once.
"""

from __future__ import annotations

import json
import os
import random
import string
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from psycopg2 import sql

import ingestion.webhook_receiver as webhook_receiver
from ingestion.callback_backlog import CallbackBacklogStore

PG_DSN = os.getenv("BACKLOG_TEST_PG_DSN", "").strip()
UI_TOKEN = "integration-ui-token"

pytestmark = pytest.mark.skipif(
    not PG_DSN, reason="BACKLOG_TEST_PG_DSN is not configured"
)


def _connect() -> Any:
    return psycopg2.connect(PG_DSN, connect_timeout=5)


@pytest.fixture()
def table_name():
    suffix = "".join(random.choices(string.ascii_lowercase, k=8))
    name = f"webhook_backlog_it_{suffix}"
    yield name
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name))
                )
    finally:
        conn.close()


@pytest.fixture()
def client(monkeypatch, table_name):
    monkeypatch.setenv("UI_TOKEN", UI_TOKEN)
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "0")
    monkeypatch.setenv("KAFKA_BROKERS", "localhost:9092")
    store = CallbackBacklogStore(_connect, table=table_name)
    app = webhook_receiver.create_app(backlog_store=store)
    app.config["TESTING"] = True
    return app.test_client(), store, table_name


def _producer(publish_raises: bool):
    producer = MagicMock()
    if publish_raises:
        producer.publish_transaction.side_effect = RuntimeError("kafka down")
        producer.publish_event.side_effect = RuntimeError("kafka down")
    else:
        producer.publish_transaction.return_value = True
        producer.publish_event.return_value = True
    return producer


def _count_rows(table_name: str) -> int:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name))
            )
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def test_failed_callback_lands_in_postgres_and_replays_once(
    client, c2b_confirmation_payload
):
    test_client, store, table_name = client

    with patch(
        "ingestion.kafka_producer.MpesaKafkaProducer",
        return_value=_producer(publish_raises=True),
    ):
        response = test_client.post(
            "/webhook/c2b/confirmation", json=c2b_confirmation_payload
        )

    assert response.status_code == 200
    assert json.loads(response.data) == {"status": "received"}
    assert _count_rows(table_name) == 1

    listed = test_client.get(
        "/admin/backlog?status=pending", headers={"X-UI-Token": UI_TOKEN}
    )
    assert listed.status_code == 200
    body = json.loads(listed.data)
    assert body["count"] == 1
    record = body["records"][0]
    assert record["callback_type"] == "c2b_confirmation"
    assert record["payload"] == c2b_confirmation_payload
    assert "kafka down" in record["failure_reason"]
    assert record["retry_count"] == 0

    record_id = record["id"]
    with patch(
        "ingestion.kafka_producer.MpesaKafkaProducer",
        return_value=_producer(publish_raises=False),
    ):
        retried = test_client.post(
            f"/admin/backlog/{record_id}/retry", headers={"X-UI-Token": UI_TOKEN}
        )
    assert retried.status_code == 200
    assert json.loads(retried.data)["status"] == "delivered"
    assert json.loads(retried.data)["retry_count"] == 1

    stored = store.get_record(record_id)
    assert stored["status"] == "delivered"
    assert stored["retry_count"] == 1

    with patch(
        "ingestion.kafka_producer.MpesaKafkaProducer",
        return_value=_producer(publish_raises=False),
    ) as producer_cls:
        again = test_client.post(
            f"/admin/backlog/{record_id}/retry", headers={"X-UI-Token": UI_TOKEN}
        )

    assert again.status_code == 409
    assert json.loads(again.data)["error"] == "already_delivered"
    producer_cls.return_value.publish_transaction.assert_not_called()
    assert store.get_record(record_id)["retry_count"] == 1


def test_failed_retry_keeps_row_pending_in_postgres(client, b2c_result_payload):
    test_client, store, table_name = client

    with patch(
        "ingestion.kafka_producer.MpesaKafkaProducer",
        return_value=_producer(publish_raises=True),
    ):
        response = test_client.post("/webhook/b2c/result", json=b2c_result_payload)

    assert response.status_code == 200
    record = store.list_records(status="pending")[0]

    with patch(
        "ingestion.kafka_producer.MpesaKafkaProducer",
        return_value=_producer(publish_raises=True),
    ):
        retried = test_client.post(
            f"/admin/backlog/{record['id']}/retry", headers={"X-UI-Token": UI_TOKEN}
        )

    assert retried.status_code == 502
    body = json.loads(retried.data)
    assert body["status"] == "pending"
    assert body["retry_count"] == 1

    stored = store.get_record(record["id"])
    assert stored["status"] == "pending"
    assert stored["retry_count"] == 1
    assert _count_rows(table_name) == 1
