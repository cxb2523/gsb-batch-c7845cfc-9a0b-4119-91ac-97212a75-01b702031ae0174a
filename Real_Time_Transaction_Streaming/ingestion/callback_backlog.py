"""Durable Postgres backlog for Safaricom callbacks that failed Kafka delivery.

The webhook receiver follows a "persist first, then deliver" contract: when a
callback cannot be published to Kafka, the raw payload is written to
``webhook_callback_backlog`` so it can be replayed later instead of being lost.
Successful publishes never create a backlog row.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from psycopg2.extras import Json

logger = logging.getLogger(__name__)

BACKLOG_TABLE = "webhook_callback_backlog"

# Terminal/working states for a backlog row.
CALLBACK_STATUSES: Tuple[str, ...] = ("pending", "delivered", "failed")

CALLBACK_TYPES: Tuple[str, ...] = ("c2b_confirmation", "b2c_result")

BACKLOG_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {BACKLOG_TABLE} (
    id BIGSERIAL PRIMARY KEY,
    callback_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    failure_reason TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retry_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    last_attempt_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    CONSTRAINT {BACKLOG_TABLE}_status_check
        CHECK (status IN ('pending', 'delivered', 'failed'))
)
"""

BACKLOG_INDEX_DDL = (
    f"""
    CREATE INDEX IF NOT EXISTS idx_{BACKLOG_TABLE}_status_received
        ON {BACKLOG_TABLE} (status, received_at DESC)
    """,
)


def build_schema_statements(table: str) -> Tuple[str, ...]:
    """Return idempotent CREATE statements for the given backlog table name."""
    create_table = BACKLOG_TABLE_DDL.replace(BACKLOG_TABLE, table, 1)
    # Replace the constraint name too, so per-test tables do not collide.
    create_table = create_table.replace(
        f"CONSTRAINT {BACKLOG_TABLE}_status_check",
        f"CONSTRAINT {table}_status_check",
        1,
    )
    index_statements = tuple(
        statement.replace(BACKLOG_TABLE, table) for statement in BACKLOG_INDEX_DDL
    )
    return (create_table, *index_statements)


_BACKLOG_COLUMNS = (
    "id",
    "callback_type",
    "payload",
    "failure_reason",
    "received_at",
    "retry_count",
    "status",
    "last_attempt_at",
    "delivered_at",
)


def _row_to_dict(row: Sequence[Any]) -> Dict[str, Any]:
    """Map a positional DB row to a dictionary with stable keys."""
    return dict(zip(_BACKLOG_COLUMNS, row))


class CallbackBacklogStore:
    """Postgres-backed store for callbacks awaiting re-delivery.

    Args:
        connect_factory: Zero-argument callable returning a new psycopg2
            connection. Kept injectable so the store is easy to test.
        table: Backlog table name (only overridden by tests).
    """

    def __init__(
        self,
        connect_factory: Callable[[], Any],
        table: str = BACKLOG_TABLE,
    ) -> None:
        if connect_factory is None:
            raise TypeError("connect_factory is required")
        self._connect = connect_factory
        self._table = table
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Create the backlog table/index if they do not exist yet.

        The webhook container does not run migrations at boot, so the store
        lazily creates its own schema on first use. ``CREATE TABLE IF NOT
        EXISTS`` is idempotent, and concurrent ``CREATE`` races are tolerated.
        """
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            conn = self._connect()
            try:
                with conn:
                    with conn.cursor() as cur:
                        for statement in build_schema_statements(self._table):
                            cur.execute(statement)
                self._schema_ready = True
            except Exception as exc:  # pragma: no cover - depends on DB state
                if "already exists" in str(exc).lower():
                    # Another worker won the CREATE race.
                    self._schema_ready = True
                    return
                raise
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def record(
        self,
        callback_type: str,
        payload: Dict[str, Any],
        failure_reason: str,
        received_at: Optional[datetime] = None,
    ) -> int:
        """Persist a callback that could not be delivered to Kafka.

        Returns:
            The id of the newly created pending backlog row.
        """
        if callback_type not in CALLBACK_TYPES:
            raise ValueError(f"Unsupported callback_type: {callback_type}")
        self.ensure_schema()
        received = received_at or datetime.now(timezone.utc)
        reason = (failure_reason or "").strip() or "unknown delivery failure"

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {self._table}
                            (callback_type, payload, failure_reason, received_at,
                             retry_count, status, last_attempt_at)
                        VALUES (%s, %s, %s, %s, 0, 'pending', NOW())
                        RETURNING id
                        """,
                        (callback_type, Json(payload), reason[:4000], received),
                    )
                    row = cur.fetchone()
            return int(row[0])
        finally:
            conn.close()

    def mark_delivered(self, record_id: int) -> Optional[Dict[str, Any]]:
        """Mark a pending row delivered after a successful replay.

        Returns:
            Updated ``{id, retry_count, status, delivered_at}``, or ``None``
            when the row is missing or was already delivered.
        """
        self.ensure_schema()
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE {self._table}
                        SET status = 'delivered',
                            delivered_at = NOW(),
                            last_attempt_at = NOW(),
                            retry_count = retry_count + 1
                        WHERE id = %s AND status <> 'delivered'
                        RETURNING id, retry_count, status, delivered_at
                        """,
                        (record_id,),
                    )
                    row = cur.fetchone()
            if row is None:
                return None
            return {
                "id": row[0],
                "retry_count": row[1],
                "status": row[2],
                "delivered_at": row[3],
            }
        finally:
            conn.close()

    def mark_retry_failed(
        self, record_id: int, failure_reason: str
    ) -> Optional[Dict[str, Any]]:
        """Record a failed replay attempt, keeping the row pending.

        Returns:
            Updated ``{id, retry_count, status}``, or ``None`` when the row is
            missing or already delivered.
        """
        self.ensure_schema()
        reason = (failure_reason or "").strip() or "unknown delivery failure"
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE {self._table}
                        SET status = 'pending',
                            failure_reason = %s,
                            last_attempt_at = NOW(),
                            retry_count = retry_count + 1
                        WHERE id = %s AND status <> 'delivered'
                        RETURNING id, retry_count, status
                        """,
                        (reason[:4000], record_id),
                    )
                    row = cur.fetchone()
            if row is None:
                return None
            return {"id": row[0], "retry_count": row[1], "status": row[2]}
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get_record(self, record_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single backlog row by id."""
        self.ensure_schema()
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT {", ".join(_BACKLOG_COLUMNS)}
                        FROM {self._table}
                        WHERE id = %s
                        """,
                        (record_id,),
                    )
                    row = cur.fetchone()
            return _row_to_dict(row) if row is not None else None
        finally:
            conn.close()

    def list_records(
        self, status: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List backlog rows, optionally filtered by status.

        Args:
            status: One of ``pending``/``delivered``/``failed``, or ``None``
                to return every row.
            limit: Maximum number of rows to return (1-500).
        """
        if status is not None and status not in CALLBACK_STATUSES:
            raise ValueError(f"Unsupported status: {status}")
        limit = max(1, min(int(limit), 500))
        self.ensure_schema()

        sql = f"SELECT {', '.join(_BACKLOG_COLUMNS)} FROM {self._table}"
        params: List[Any] = []
        if status is not None:
            sql += " WHERE status = %s"
            params.append(status)
        sql += " ORDER BY received_at DESC, id DESC LIMIT %s"
        params.append(limit)

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    rows = cur.fetchall() or []
            return [_row_to_dict(row) for row in rows]
        finally:
            conn.close()
