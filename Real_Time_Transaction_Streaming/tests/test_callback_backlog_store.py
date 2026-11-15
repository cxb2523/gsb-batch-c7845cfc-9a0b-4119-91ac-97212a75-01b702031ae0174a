"""Tests for the Postgres-backed ``CallbackBacklogStore``.

These run against a real PostgreSQL instance when ``BACKLOG_TEST_PG_DSN`` is
set (for example ``postgresql://postgres:postgres@localhost:5432/postgres``);
otherwise they are skipped so the default test suite needs no database.
"""

from __future__ import annotations

import os
import random
import string
from datetime import datetime, timezone
from typing import Any, Dict, Iterator

import psycopg2
import pytest
from psycopg2 import sql

from ingestion.callback_backlog import CallbackBacklogStore

PG_DSN = os.getenv("BACKLOG_TEST_PG_DSN", "").strip()

pytestmark = pytest.mark.skipif(
    not PG_DSN, reason="BACKLOG_TEST_PG_DSN is not configured"
)


def _connect() -> Any:
    return psycopg2.connect(PG_DSN, connect_timeout=5)


@pytest.fixture()
def table_name() -> Iterator[str]:
    """Create an isolated backlog table and drop it afterwards."""
    suffix = "".join(random.choices(string.ascii_lowercase, k=8))
    name = f"webhook_callback_backlog_test_{suffix}"
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
def store(table_name: str) -> CallbackBacklogStore:
    return CallbackBacklogStore(_connect, table=table_name)


def _payload(trans_id: str = "TXN1") -> Dict[str, Any]:
    return {"TransID": trans_id, "MSISDN": "254712345678", "TransAmount": "500"}


def test_ensure_schema_creates_table(
    store: CallbackBacklogStore, table_name: str
) -> None:
    store.ensure_schema()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (table_name,),
            )
            columns = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    assert {
        "id",
        "callback_type",
        "payload",
        "failure_reason",
        "received_at",
        "retry_count",
        "status",
    } <= columns


def test_record_inserts_pending_row(store: CallbackBacklogStore) -> None:
    received_at = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    record_id = store.record(
        callback_type="c2b_confirmation",
        payload=_payload(),
        failure_reason="broker unavailable",
        received_at=received_at,
    )

    row = store.get_record(record_id)
    assert row is not None
    assert row["callback_type"] == "c2b_confirmation"
    assert row["payload"] == _payload()
    assert row["failure_reason"] == "broker unavailable"
    assert row["status"] == "pending"
    assert row["retry_count"] == 0
    assert row["delivered_at"] is None
    assert row["received_at"].replace(tzinfo=None) == received_at.replace(tzinfo=None)


def test_record_rejects_unknown_callback_type(store: CallbackBacklogStore) -> None:
    with pytest.raises(ValueError):
        store.record("stk_callback", _payload(), "nope")


def test_list_records_filters_by_status_and_orders_newest_first(
    store: CallbackBacklogStore,
) -> None:
    first = store.record("c2b_confirmation", _payload("OLD"), "down")
    second = store.record("b2c_result", _payload("NEW"), "down")

    pending = store.list_records(status="pending")
    assert [row["id"] for row in pending] == [second, first]
    assert store.list_records(status="delivered") == []

    store.mark_delivered(first)
    delivered = store.list_records(status="delivered")
    assert [row["id"] for row in delivered] == [first]
    assert [row["id"] for row in store.list_records(status="pending")] == [second]


def test_list_records_rejects_unknown_status(store: CallbackBacklogStore) -> None:
    with pytest.raises(ValueError):
        store.list_records(status="bogus")


def test_mark_delivered_updates_status_and_retry_count(
    store: CallbackBacklogStore,
) -> None:
    record_id = store.record("c2b_confirmation", _payload(), "down")

    updated = store.mark_delivered(record_id)
    assert updated is not None
    assert updated["status"] == "delivered"
    assert updated["retry_count"] == 1
    assert updated["delivered_at"] is not None

    row = store.get_record(record_id)
    assert row["status"] == "delivered"
    assert row["retry_count"] == 1


def test_mark_delivered_is_idempotent(store: CallbackBacklogStore) -> None:
    """An already-delivered row must never be transitioned again."""
    record_id = store.record("b2c_result", _payload(), "down")

    assert store.mark_delivered(record_id) is not None
    assert store.mark_delivered(record_id) is None
    assert store.get_record(record_id)["retry_count"] == 1


def test_mark_delivered_missing_row_returns_none(store: CallbackBacklogStore) -> None:
    store.ensure_schema()
    assert store.mark_delivered(987654321) is None


def test_mark_retry_failed_keeps_pending_and_bumps_count(
    store: CallbackBacklogStore,
) -> None:
    record_id = store.record("c2b_confirmation", _payload(), "down")

    first = store.mark_retry_failed(record_id, "still down")
    second = store.mark_retry_failed(record_id, "still down again")

    assert first["status"] == "pending" and first["retry_count"] == 1
    assert second["status"] == "pending" and second["retry_count"] == 2
    row = store.get_record(record_id)
    assert row["failure_reason"] == "still down again"
    assert row["status"] == "pending"


def test_mark_retry_failed_ignores_delivered_row(store: CallbackBacklogStore) -> None:
    record_id = store.record("c2b_confirmation", _payload(), "down")
    store.mark_delivered(record_id)

    assert store.mark_retry_failed(record_id, "late failure") is None
    row = store.get_record(record_id)
    assert row["status"] == "delivered"
    assert row["retry_count"] == 1


def test_missing_record_returns_none(store: CallbackBacklogStore) -> None:
    assert store.get_record(123456789) is None


def test_store_reuses_cached_schema_flag(store: CallbackBacklogStore) -> None:
    store.ensure_schema()
    assert store._schema_ready is True
    # A second call must be a no-op rather than re-running DDL.
    store.ensure_schema()
    assert store._schema_ready is True
