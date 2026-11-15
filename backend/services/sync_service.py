"""Per-account Drive synchronisation with settlement reporting.

The Google API client lives in :mod:`services.drive_service`; this module only
depends on the database models.  That keeps the settlement logic testable with
fake account listings and simulated failures (no Google credentials needed).

The latest settlement is kept in-process so the read-only
``GET /api/files/sync/status`` endpoint can report what happened during the
most recent background sync.
"""

from __future__ import annotations

import copy
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from models.models import DriveAccount, File

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 500

ListFilesFn = Callable[[DriveAccount], list[dict]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_drive_time(s: str | None) -> datetime:
    if not s:
        return datetime.utcnow()
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)


def _summarize_error(exc: BaseException) -> str:
    """Return a short, human-readable reason without leaking a whole traceback."""
    message = str(exc).strip()
    summary = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    if len(summary) > MAX_ERROR_LENGTH:
        summary = summary[: MAX_ERROR_LENGTH - 1] + "…"
    return summary


@dataclass
class AccountSyncResult:
    """Outcome of syncing one Drive account."""

    account_index: int
    success: bool
    email: str | None = None
    file_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "account_index": self.account_index,
            "email": self.email,
            "success": self.success,
            "file_count": self.file_count,
            "created_count": self.created_count,
            "updated_count": self.updated_count,
            "deleted_count": self.deleted_count,
            "error": self.error,
        }


@dataclass
class SyncSettlement:
    """Aggregated result of one sync run, one entry per connected account."""

    run_id: str
    started_at: str
    finished_at: str | None
    accounts: list[AccountSyncResult] = field(default_factory=list)
    error: str | None = None

    @property
    def succeeded_count(self) -> int:
        return sum(1 for a in self.accounts if a.success)

    @property
    def failed_count(self) -> int:
        return sum(1 for a in self.accounts if not a.success)

    @property
    def total_files(self) -> int:
        return sum(a.file_count for a in self.accounts)

    @property
    def total_deleted(self) -> int:
        return sum(a.deleted_count for a in self.accounts)

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        if not self.accounts:
            return "no_accounts"
        if self.failed_count == 0:
            return "success"
        if self.succeeded_count == 0:
            return "failed"
        return "partial"

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "account_count": len(self.accounts),
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "total_files": self.total_files,
            "total_deleted": self.total_deleted,
            "error": self.error,
            "accounts": [a.to_dict() for a in self.accounts],
        }


# ── Latest-settlement registry ────────────────────────────────────────────────

_registry_lock = threading.Lock()
_latest_settlement: dict | None = None


def begin_sync(run_id: str | None = None, account_indices: Iterable[int] | None = None) -> dict:
    """Mark a sync as running so clients can distinguish it from an old result.

    Called synchronously by the HTTP route (before the background task starts)
    and again by the background job itself.  A repeated call for the same run
    keeps the original start time.
    """
    global _latest_settlement

    if not run_id:
        run_id = uuid.uuid4().hex
    indices = list(account_indices) if account_indices is not None else None

    with _registry_lock:
        if (
            _latest_settlement is not None
            and _latest_settlement.get("run_id") == run_id
            and _latest_settlement.get("status") == "running"
        ):
            if indices is not None:
                _latest_settlement["account_count"] = len(indices)
            return copy.deepcopy(_latest_settlement)

        _latest_settlement = {
            "run_id": run_id,
            "status": "running",
            "started_at": _utc_now_iso(),
            "finished_at": None,
            "account_count": len(indices) if indices is not None else 0,
            "succeeded_count": 0,
            "failed_count": 0,
            "total_files": 0,
            "total_deleted": 0,
            "error": None,
            "accounts": [],
        }
        return copy.deepcopy(_latest_settlement)


def finish_sync(settlement: SyncSettlement) -> bool:
    """Publish the final settlement unless a newer run has superseded it."""
    global _latest_settlement

    payload = settlement.to_dict()
    with _registry_lock:
        if (
            _latest_settlement is not None
            and _latest_settlement.get("run_id") != settlement.run_id
        ):
            logger.warning(
                "Discarding settlement for superseded sync run %s (latest is %s)",
                settlement.run_id,
                _latest_settlement.get("run_id"),
            )
            return False
        _latest_settlement = payload
        return True


def get_latest_settlement() -> dict | None:
    with _registry_lock:
        return copy.deepcopy(_latest_settlement)


# ── Sync implementation ────────────────────────────────────────────────────────


def _sync_account(
    db: Session,
    account: DriveAccount,
    list_files_fn: ListFilesFn,
) -> tuple[int, int, int]:
    """Synchronise one account in its own transaction.

    Returns ``(created, updated, deleted)``.  Raises on any failure; the
    caller rolls back only this account's transaction.
    """
    drive_files = list_files_fn(account) or []
    drive_ids = {df["id"] for df in drive_files if df.get("id")}

    existing_rows = (
        db.query(File).filter(File.account_index == account.account_index).all()
    )
    existing_by_id: dict[str, File] = {}
    for row in existing_rows:
        existing_by_id.setdefault(row.drive_file_id, row)

    deleted_count = 0
    for row in existing_rows:
        if row.drive_file_id not in drive_ids:
            db.delete(row)
            deleted_count += 1

    created_count = 0
    updated_count = 0
    seen_ids: set[str] = set()
    for df in drive_files:
        drive_file_id = df.get("id")
        if not drive_file_id or drive_file_id in seen_ids:
            continue
        seen_ids.add(drive_file_id)

        parent = df.get("parents", [None])[0] if df.get("parents") else None
        existing = existing_by_id.get(drive_file_id)
        if existing:
            existing.file_name = df.get("name", existing.file_name)
            existing.size = int(df.get("size") or 0)
            existing.thumbnail_link = df.get("thumbnailLink")
            existing.parent_drive_file_id = parent
            updated_count += 1
        else:
            db.add(File(
                file_name=df.get("name", ""),
                drive_file_id=drive_file_id,
                account_index=account.account_index,
                size=int(df.get("size") or 0),
                mime_type=df.get("mimeType"),
                thumbnail_link=df.get("thumbnailLink"),
                parent_drive_file_id=parent,
                created_at=_parse_drive_time(df.get("createdTime")),
            ))
            created_count += 1

    # Commit per account: a later failure cannot roll this account back.
    db.commit()
    return created_count, updated_count, deleted_count


def sync_files_from_drives(
    db: Session,
    *,
    list_files_fn: ListFilesFn,
    run_id: str | None = None,
) -> SyncSettlement:
    """Pull the remote file list for every connected account.

    Each account is fetched and committed independently.  A failure is recorded
    in that account's settlement entry, rolled back, and does not affect
    accounts that already committed or accounts processed afterwards.
    """
    run_id = run_id or uuid.uuid4().hex

    try:
        account_rows = (
            db.query(DriveAccount)
            .filter(DriveAccount.is_connected == True)  # noqa: E712
            .all()
        )
    except Exception as exc:  # pragma: no cover - defensive, DB-level failure
        logger.exception("Could not load connected accounts for sync %s", run_id)
        settlement = SyncSettlement(
            run_id=run_id,
            started_at=begin_sync(run_id)["started_at"],
            finished_at=_utc_now_iso(),
            accounts=[],
            error=_summarize_error(exc),
        )
        finish_sync(settlement)
        return settlement

    snapshots = [(acc.account_index, acc.email) for acc in account_rows]

    started = begin_sync(run_id, [idx for idx, _ in snapshots])
    results: list[AccountSyncResult] = []

    for account_index, email in snapshots:
        try:
            # Re-read the row so a session rollback from an earlier account can
            # never leave this account's attributes expired/stale.
            account = (
                db.query(DriveAccount)
                .filter(DriveAccount.account_index == account_index)
                .first()
            )
            if account is None:
                raise LookupError(f"Account #{account_index} no longer exists")

            created, updated, deleted = _sync_account(db, account, list_files_fn)
            results.append(AccountSyncResult(
                account_index=account_index,
                email=email,
                success=True,
                file_count=created + updated,
                created_count=created,
                updated_count=updated,
                deleted_count=deleted,
            ))
            logger.info(
                "Sync account #%s complete: %s created, %s updated, %s deleted",
                account_index,
                created,
                updated,
                deleted,
            )
        except Exception as exc:
            try:
                db.rollback()
            except Exception:  # pragma: no cover - defensive
                logger.exception("Rollback failed for sync account #%s", account_index)

            summary = _summarize_error(exc)
            logger.exception("Sync account #%s failed: %s", account_index, summary)
            results.append(AccountSyncResult(
                account_index=account_index,
                email=email,
                success=False,
                error=summary,
            ))

    settlement = SyncSettlement(
        run_id=run_id,
        started_at=started["started_at"],
        finished_at=_utc_now_iso(),
        accounts=results,
    )
    finish_sync(settlement)
    return settlement
