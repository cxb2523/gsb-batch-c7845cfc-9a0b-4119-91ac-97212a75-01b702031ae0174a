"""Tests for the callback backlog ("persist first, then deliver") flow.

Covers both callback chains (C2B confirmation and B2C result):

* successful Kafka delivery returns 200 and creates no backlog row,
* failed Kafka delivery still returns 200 and parks one pending row,
* the token-protected listing/retry endpoints enforce UI_TOKEN,
* retrying a delivered record never publishes it twice.

The routes are exercised with an in-memory backlog store so the tests do not
need a live Postgres instance; the SQL-backed store has its own tests in
``test_callback_backlog_store.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

import ingestion.webhook_receiver as webhook_receiver

UI_TOKEN = "test-ui-token"


class InMemoryCallbackBacklogStore:
    """Behavioural stand-in for ``CallbackBacklogStore`` used by route tests."""

    def __init__(self) -> None:
        self.rows: Dict[int, Dict[str, Any]] = {}
        self._next_id = 1
        self.schema_ensured = False

    def ensure_schema(self) -> None:
        self.schema_ensured = True

    def record(
        self,
        callback_type: str,
        payload: Dict[str, Any],
        failure_reason: str,
        received_at: Optional[datetime] = None,
    ) -> int:
        record_id = self._next_id
        self._next_id += 1
        self.rows[record_id] = {
            "id": record_id,
            "callback_type": callback_type,
            "payload": payload,
            "failure_reason": failure_reason,
            "received_at": received_at or datetime.now(timezone.utc),
            "retry_count": 0,
            "status": "pending",
            "last_attempt_at": None,
            "delivered_at": None,
        }
        return record_id

    def mark_delivered(self, record_id: int) -> Optional[Dict[str, Any]]:
        row = self.rows.get(record_id)
        if row is None or row["status"] == "delivered":
            return None
        row["status"] = "delivered"
        row["retry_count"] += 1
        row["delivered_at"] = datetime.now(timezone.utc)
        row["last_attempt_at"] = row["delivered_at"]
        return {
            "id": record_id,
            "retry_count": row["retry_count"],
            "status": row["status"],
            "delivered_at": row["delivered_at"],
        }

    def mark_retry_failed(
        self, record_id: int, failure_reason: str
    ) -> Optional[Dict[str, Any]]:
        row = self.rows.get(record_id)
        if row is None or row["status"] == "delivered":
            return None
        row["status"] = "pending"
        row["retry_count"] += 1
        row["failure_reason"] = failure_reason
        row["last_attempt_at"] = datetime.now(timezone.utc)
        return {
            "id": record_id,
            "retry_count": row["retry_count"],
            "status": row["status"],
        }

    def get_record(self, record_id: int) -> Optional[Dict[str, Any]]:
        return self.rows.get(record_id)

    def list_records(
        self, status: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        rows = [
            row
            for row in self.rows.values()
            if status is None or row["status"] == status
        ]
        rows.sort(key=lambda r: (r["received_at"], r["id"]), reverse=True)
        return rows[: max(1, min(int(limit), 500))]


@pytest.fixture
def backlog_store() -> InMemoryCallbackBacklogStore:
    return InMemoryCallbackBacklogStore()


@pytest.fixture
def backlog_app(monkeypatch, backlog_store) -> Flask:
    """Flask app wired to an in-memory backlog and a fixed UI_TOKEN."""
    monkeypatch.setenv("UI_TOKEN", UI_TOKEN)
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "0")
    monkeypatch.setenv("KAFKA_BROKERS", "localhost:9092")
    app = webhook_receiver.create_app(backlog_store=backlog_store)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def backlog_client(backlog_app):
    return backlog_app.test_client()


def _auth_headers(token: str = UI_TOKEN) -> Dict[str, str]:
    return {"X-UI-Token": token}


def _decode(response) -> Dict[str, Any]:
    return json.loads(response.data)


def _producer_mock(
    method: str, *, returns: Any = True, raises: Optional[Exception] = None
):
    """Patch MpesaKafkaProducer so webhook routes receive a controlled producer."""
    producer = MagicMock()
    producer.publish_transaction.return_value = True
    producer.publish_event.return_value = True
    target = getattr(producer, method)
    if raises is not None:
        target.side_effect = raises
    else:
        target.return_value = returns
    patcher = patch(
        "ingestion.kafka_producer.MpesaKafkaProducer", return_value=producer
    )
    return patcher, producer


# =====================================================================
# Happy path: Kafka delivers, nothing lands in the backlog
# =====================================================================


class TestSuccessfulDelivery:
    def test_c2b_confirmation_success_returns_200_and_no_backlog(
        self, backlog_client, backlog_store, c2b_confirmation_payload
    ):
        patcher, producer = _producer_mock("publish_transaction")
        with patcher:
            response = backlog_client.post(
                "/webhook/c2b/confirmation", json=c2b_confirmation_payload
            )

        assert response.status_code == 200
        assert _decode(response) == {"status": "received"}
        assert producer.publish_transaction.call_count == 1
        assert backlog_store.rows == {}

    def test_b2c_result_success_returns_200_and_no_backlog(
        self, backlog_client, backlog_store, b2c_result_payload
    ):
        patcher, producer = _producer_mock("publish_event")
        with patcher:
            response = backlog_client.post(
                "/webhook/b2c/result", json=b2c_result_payload
            )

        assert response.status_code == 200
        assert _decode(response) == {"status": "received"}
        assert producer.publish_event.call_count == 1
        assert backlog_store.rows == {}


# =====================================================================
# Kafka failure: acknowledge 200, persist exactly one pending row
# =====================================================================


class TestDeliveryFailurePersistsBacklog:
    def test_c2b_confirmation_kafka_exception_returns_200_and_pending_row(
        self, backlog_client, backlog_store, c2b_confirmation_payload
    ):
        patcher, _ = _producer_mock(
            "publish_transaction", raises=RuntimeError("broker unavailable")
        )
        with patcher:
            response = backlog_client.post(
                "/webhook/c2b/confirmation", json=c2b_confirmation_payload
            )

        assert response.status_code == 200
        assert _decode(response) == {"status": "received"}
        assert len(backlog_store.rows) == 1

        row = next(iter(backlog_store.rows.values()))
        assert row["callback_type"] == "c2b_confirmation"
        assert row["payload"] == c2b_confirmation_payload
        assert "broker unavailable" in row["failure_reason"]
        assert row["status"] == "pending"
        assert row["retry_count"] == 0
        assert row["received_at"] is not None

    def test_b2c_result_kafka_exception_returns_200_and_pending_row(
        self, backlog_client, backlog_store, b2c_result_payload
    ):
        patcher, _ = _producer_mock(
            "publish_event", raises=RuntimeError("delivery timeout")
        )
        with patcher:
            response = backlog_client.post(
                "/webhook/b2c/result", json=b2c_result_payload
            )

        assert response.status_code == 200
        assert _decode(response) == {"status": "received"}
        assert len(backlog_store.rows) == 1
        row = next(iter(backlog_store.rows.values()))
        assert row["callback_type"] == "b2c_result"
        assert row["payload"] == b2c_result_payload
        assert "delivery timeout" in row["failure_reason"]
        assert row["status"] == "pending"

    def test_producer_returning_false_also_persists_backlog(
        self, backlog_client, backlog_store, c2b_confirmation_payload
    ):
        """A producer that swallows errors and returns False is treated as failure."""
        patcher, _ = _producer_mock("publish_transaction", returns=False)
        with patcher:
            response = backlog_client.post(
                "/webhook/c2b/confirmation", json=c2b_confirmation_payload
            )

        assert response.status_code == 200
        assert len(backlog_store.rows) == 1
        assert backlog_store.rows[1]["status"] == "pending"

    def test_producer_init_failure_also_persists_backlog(
        self, backlog_client, backlog_store, c2b_confirmation_payload
    ):
        """A producer that cannot even be constructed is treated as failure."""
        with patch(
            "ingestion.kafka_producer.MpesaKafkaProducer",
            side_effect=RuntimeError("missing confluent-kafka"),
        ):
            response = backlog_client.post(
                "/webhook/c2b/confirmation", json=c2b_confirmation_payload
            )

        assert response.status_code == 200
        assert len(backlog_store.rows) == 1
        row = backlog_store.rows[1]
        assert row["status"] == "pending"
        assert "missing confluent-kafka" in row["failure_reason"]

    def test_backlog_preserves_raw_payload_verbatim(
        self, backlog_client, backlog_store
    ):
        """Unknown/extra Daraja fields must survive round-trip into the backlog."""
        raw_payload = {
            "TransID": "TXN-RAW-1",
            "TransAmount": "1234.50",
            "MSISDN": "254712345678",
            "TransTime": "20240514120000",
            "ThirdPartyTransID": "3rd-party-123",
            "OrgAccountBalance": "10000.00",
            "Nested": {"Item": [{"Name": "Amount", "Value": 1}]},
        }
        patcher, _ = _producer_mock(
            "publish_transaction", raises=RuntimeError("broker down")
        )
        with patcher:
            backlog_client.post("/webhook/c2b/confirmation", json=raw_payload)

        row = next(iter(backlog_store.rows.values()))
        assert row["payload"] == raw_payload

    def test_backlog_write_failure_still_returns_200(
        self, backlog_app, c2b_confirmation_payload
    ):
        """Both Kafka and Postgres down: still acknowledge to Safaricom."""

        class BrokenStore(InMemoryCallbackBacklogStore):
            def record(self, *args, **kwargs):
                raise RuntimeError("postgres unavailable")

        broken_app = webhook_receiver.create_app(backlog_store=BrokenStore())
        broken_app.config["TESTING"] = True
        client = broken_app.test_client()

        patcher, _ = _producer_mock(
            "publish_transaction", raises=RuntimeError("broker unavailable")
        )
        with patcher:
            response = client.post(
                "/webhook/c2b/confirmation", json=c2b_confirmation_payload
            )

        assert response.status_code == 200
        assert _decode(response) == {"status": "received"}


# =====================================================================
# Listing endpoint
# =====================================================================


class TestBacklogListing:
    def _seed(self, store, callback_type="c2b_confirmation", count=1):
        ids = []
        for index in range(count):
            ids.append(
                store.record(
                    callback_type=callback_type,
                    payload={"TransID": f"TXN{index}"},
                    failure_reason="kafka down",
                )
            )
        return ids

    def test_list_without_token_returns_401(self, backlog_client, backlog_store):
        self._seed(backlog_store)
        response = backlog_client.get("/admin/backlog")
        assert response.status_code == 401

    def test_list_with_wrong_token_returns_401(self, backlog_client, backlog_store):
        self._seed(backlog_store)
        response = backlog_client.get(
            "/admin/backlog", headers={"X-UI-Token": "wrong-token"}
        )
        assert response.status_code == 401

    def test_list_with_correct_header_token(self, backlog_client, backlog_store):
        self._seed(backlog_store, count=2)
        response = backlog_client.get("/admin/backlog", headers=_auth_headers())
        assert response.status_code == 200
        body = _decode(response)
        assert body["count"] == 2
        assert len(body["records"]) == 2
        assert all(r["status"] == "pending" for r in body["records"])

    def test_list_accepts_token_query_parameter(self, backlog_client, backlog_store):
        self._seed(backlog_store)
        response = backlog_client.get(f"/admin/backlog?token={UI_TOKEN}")
        assert response.status_code == 200
        assert _decode(response)["count"] == 1

    def test_list_filters_by_status(self, backlog_client, backlog_store):
        pending_id = self._seed(backlog_store)[0]
        backlog_store.rows[pending_id]["status"] = "delivered"

        response = backlog_client.get(
            "/admin/backlog?status=delivered", headers=_auth_headers()
        )
        assert response.status_code == 200
        body = _decode(response)
        assert body["count"] == 1
        assert body["records"][0]["id"] == pending_id

        pending_only = backlog_client.get(
            "/admin/backlog?status=pending", headers=_auth_headers()
        )
        assert _decode(pending_only)["count"] == 0

    def test_list_rejects_unknown_status(self, backlog_client):
        response = backlog_client.get(
            "/admin/backlog?status=bogus", headers=_auth_headers()
        )
        assert response.status_code == 400

    def test_list_is_401_when_ui_token_not_configured(self, monkeypatch, backlog_store):
        """Fails closed: no configured token must never expose the backlog."""
        monkeypatch.delenv("UI_TOKEN", raising=False)
        app = webhook_receiver.create_app(backlog_store=backlog_store)
        app.config["TESTING"] = True
        response = app.test_client().get("/admin/backlog")
        assert response.status_code == 401


# =====================================================================
# Retry endpoint
# =====================================================================


class TestBacklogRetry:
    def _seed(self, store, callback_type="c2b_confirmation", payload=None):
        return store.record(
            callback_type=callback_type,
            payload=payload or {"TransID": "TXN1", "MSISDN": "254712345678"},
            failure_reason="kafka down",
        )

    def test_retry_without_token_returns_401_and_does_not_send(
        self, backlog_client, backlog_store
    ):
        record_id = self._seed(backlog_store)
        patcher, producer = _producer_mock("publish_transaction")
        with patcher:
            response = backlog_client.post(f"/admin/backlog/{record_id}/retry")

        assert response.status_code == 401
        producer.publish_transaction.assert_not_called()
        assert backlog_store.rows[record_id]["status"] == "pending"

    def test_retry_unknown_record_returns_404(self, backlog_client):
        patcher, producer = _producer_mock("publish_transaction")
        with patcher:
            response = backlog_client.post(
                "/admin/backlog/9999/retry", headers=_auth_headers()
            )

        assert response.status_code == 404
        producer.publish_transaction.assert_not_called()

    def test_retry_success_marks_delivered_and_increments_retry_count(
        self, backlog_client, backlog_store
    ):
        record_id = self._seed(backlog_store)
        patcher, producer = _producer_mock("publish_transaction")
        with patcher:
            response = backlog_client.post(
                f"/admin/backlog/{record_id}/retry", headers=_auth_headers()
            )

        assert response.status_code == 200
        body = _decode(response)
        assert body["status"] == "delivered"
        assert body["retry_count"] == 1
        producer.publish_transaction.assert_called_once()

        row = backlog_store.rows[record_id]
        assert row["status"] == "delivered"
        assert row["retry_count"] == 1
        assert row["delivered_at"] is not None

    def test_retry_uses_callback_type_specific_publisher_for_b2c(
        self, backlog_client, backlog_store, b2c_result_payload
    ):
        record_id = self._seed(
            backlog_store, callback_type="b2c_result", payload=b2c_result_payload
        )
        patcher, producer = _producer_mock("publish_event")
        with patcher:
            response = backlog_client.post(
                f"/admin/backlog/{record_id}/retry", headers=_auth_headers()
            )

        assert response.status_code == 200
        producer.publish_event.assert_called_once()

    def test_retrying_delivered_record_never_publishes_again(
        self, backlog_client, backlog_store
    ):
        record_id = self._seed(backlog_store)
        patcher, producer = _producer_mock("publish_transaction")
        with patcher:
            first = backlog_client.post(
                f"/admin/backlog/{record_id}/retry", headers=_auth_headers()
            )
            second = backlog_client.post(
                f"/admin/backlog/{record_id}/retry", headers=_auth_headers()
            )

        assert first.status_code == 200
        assert second.status_code == 409
        assert _decode(second)["error"] == "already_delivered"
        assert producer.publish_transaction.call_count == 1
        assert backlog_store.rows[record_id]["retry_count"] == 1

    def test_retry_failure_keeps_pending_and_increments_retry_count(
        self, backlog_client, backlog_store
    ):
        record_id = self._seed(backlog_store)
        patcher, producer = _producer_mock(
            "publish_transaction", raises=RuntimeError("still down")
        )
        with patcher:
            response = backlog_client.post(
                f"/admin/backlog/{record_id}/retry", headers=_auth_headers()
            )

        assert response.status_code == 502
        body = _decode(response)
        assert body["status"] == "pending"
        assert body["retry_count"] == 1
        assert "still down" in body["failure_reason"]

        row = backlog_store.rows[record_id]
        assert row["status"] == "pending"
        assert row["retry_count"] == 1
        assert "still down" in row["failure_reason"]

    def test_second_failed_retry_accumulates_count(self, backlog_client, backlog_store):
        record_id = self._seed(backlog_store)
        patcher, _ = _producer_mock(
            "publish_transaction", raises=RuntimeError("still down")
        )
        with patcher:
            backlog_client.post(
                f"/admin/backlog/{record_id}/retry", headers=_auth_headers()
            )
            second = backlog_client.post(
                f"/admin/backlog/{record_id}/retry", headers=_auth_headers()
            )

        assert second.status_code == 502
        assert backlog_store.rows[record_id]["retry_count"] == 2
        assert backlog_store.rows[record_id]["status"] == "pending"


# =====================================================================
# Wiring checks
# =====================================================================


class TestBacklogWiring:
    def test_default_store_is_lazily_created_from_pg_connect(self, monkeypatch):
        """Without an injected store the app builds the Postgres-backed store."""
        monkeypatch.setenv("UI_TOKEN", UI_TOKEN)
        app = webhook_receiver.create_app()
        app.config["TESTING"] = True

        with patch("ingestion.webhook_receiver.CallbackBacklogStore") as store_cls:
            store_cls.return_value.list_records.return_value = []
            response = app.test_client().get("/admin/backlog", headers=_auth_headers())

        assert response.status_code == 200
        assert _decode(response)["count"] == 0
        store_cls.assert_called_once()

    def test_backlog_routes_registered(self, backlog_app):
        rules = {rule.rule for rule in backlog_app.url_map.iter_rules()}
        assert "/admin/backlog" in rules
        assert "/admin/backlog/<int:record_id>/retry" in rules
