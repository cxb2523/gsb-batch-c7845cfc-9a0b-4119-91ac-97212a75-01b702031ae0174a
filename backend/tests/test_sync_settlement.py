"""Tests for per-account sync settlement (no Google credentials required)."""

import pytest

from models.models import File
from services import sync_service
from services.sync_service import (
    begin_sync,
    finish_sync,
    get_latest_settlement,
    sync_files_from_drives,
)

from .conftest import drive_file


@pytest.fixture(autouse=True)
def reset_registry(monkeypatch):
    """Each test starts with no previously published settlement."""
    monkeypatch.setattr(sync_service, "_latest_settlement", None)
    yield


def test_all_accounts_succeed(db_session, add_account, add_file):
    add_account(1)
    add_account(2, email="second@example.com")
    add_file(1, "stale-file", name="stale.bin")

    listings = {
        1: [drive_file("a1"), drive_file("a2", name="two.bin", size=20)],
        2: [drive_file("b1")],
    }

    def fake_list(account):
        return listings[account.account_index]

    settlement = sync_files_from_drives(db_session, list_files_fn=fake_list, run_id="run-ok")

    assert settlement.status == "success"
    assert settlement.succeeded_count == 2
    assert settlement.failed_count == 0
    assert settlement.error is None

    by_index = {a.account_index: a for a in settlement.accounts}
    assert by_index[1].success is True
    assert by_index[1].file_count == 2
    assert by_index[1].created_count == 2
    assert by_index[1].updated_count == 0
    assert by_index[1].deleted_count == 1  # stale-file was removed
    assert by_index[1].error is None
    assert by_index[2].file_count == 1

    rows = db_session.query(File).filter(File.account_index == 1).all()
    assert {r.drive_file_id for r in rows} == {"a1", "a2"}

    published = get_latest_settlement()
    assert published["run_id"] == "run-ok"
    assert published["status"] == "success"
    assert published["succeeded_count"] == 2


def test_failure_is_isolated_and_later_accounts_still_run(db_session, add_account, add_file):
    add_account(1)
    add_account(2)
    add_account(3)
    add_file(2, "old-b", name="old-b.bin")

    def fake_list(account):
        if account.account_index == 1:
            raise RuntimeError("quota exceeded for account one")
        if account.account_index == 2:
            return [drive_file("b-new")]
        return [drive_file("c-new")]

    settlement = sync_files_from_drives(db_session, list_files_fn=fake_list, run_id="run-partial")

    assert settlement.status == "partial"
    assert settlement.succeeded_count == 2
    assert settlement.failed_count == 1
    assert [a.account_index for a in settlement.accounts] == [1, 2, 3]

    failed = settlement.accounts[0]
    assert failed.account_index == 1
    assert failed.success is False
    assert "RuntimeError" in failed.error
    assert "quota exceeded" in failed.error
    assert failed.file_count == 0

    # Account 2 committed after the failure and stale rows were removed.
    assert settlement.accounts[1].success is True
    assert db_session.query(File).filter(File.drive_file_id == "b-new").count() == 1
    assert db_session.query(File).filter(File.drive_file_id == "old-b").count() == 0

    # Account 3 ran after the failure too.
    assert settlement.accounts[2].success is True
    assert db_session.query(File).filter(File.drive_file_id == "c-new").count() == 1


def test_failure_does_not_rollback_already_committed_accounts(db_session, add_account):
    add_account(1)
    add_account(2)

    def fake_list(account):
        if account.account_index == 1:
            return [drive_file("keep-me")]
        raise RuntimeError("boom")

    settlement = sync_files_from_drives(db_session, list_files_fn=fake_list, run_id="run-keep")

    assert settlement.accounts[0].success is True
    assert settlement.accounts[1].success is False

    db_session.expire_all()
    assert db_session.query(File).filter(File.drive_file_id == "keep-me").count() == 1


def test_all_accounts_fail_reports_overall_failure(db_session, add_account):
    add_account(1)
    add_account(2)

    def fake_list(account):
        raise ConnectionError(f"cannot reach drive for {account.account_index}")

    settlement = sync_files_from_drives(db_session, list_files_fn=fake_list, run_id="run-fail")

    assert settlement.status == "failed"
    assert settlement.succeeded_count == 0
    assert settlement.failed_count == 2
    assert all(a.success is False for a in settlement.accounts)
    assert all("ConnectionError" in a.error for a in settlement.accounts)
    assert get_latest_settlement()["status"] == "failed"


def test_no_connected_accounts(db_session, add_account):
    add_account(1, connected=False)

    settlement = sync_files_from_drives(db_session, list_files_fn=lambda a: [], run_id="run-none")

    assert settlement.accounts == []
    assert settlement.status == "no_accounts"
    assert settlement.succeeded_count == 0
    assert settlement.failed_count == 0


def test_duplicate_remote_ids_are_created_once(db_session, add_account):
    add_account(1)

    settlement = sync_files_from_drives(
        db_session,
        list_files_fn=lambda a: [drive_file("dup"), drive_file("dup")],
        run_id="run-dup",
    )

    assert settlement.accounts[0].success is True
    assert settlement.accounts[0].file_count == 1
    assert db_session.query(File).filter(File.drive_file_id == "dup").count() == 1


def test_error_summary_is_truncated(db_session, add_account):
    add_account(1)
    long_message = "x" * 5000

    def fake_list(account):
        raise ValueError(long_message)

    settlement = sync_files_from_drives(db_session, list_files_fn=fake_list, run_id="run-long")

    error = settlement.accounts[0].error
    assert len(error) <= sync_service.MAX_ERROR_LENGTH
    assert error.endswith("…")


def test_published_settlement_has_running_state_then_final(db_session, add_account):
    add_account(1)
    begin_sync("run-live", [1])

    running = get_latest_settlement()
    assert running["status"] == "running"
    assert running["run_id"] == "run-live"
    assert running["account_count"] == 1
    assert running["accounts"] == []

    sync_files_from_drives(db_session, list_files_fn=lambda a: [drive_file("x")], run_id="run-live")

    final = get_latest_settlement()
    assert final["status"] == "success"
    assert final["finished_at"] is not None
    assert final["accounts"][0]["account_index"] == 1


def test_stale_run_cannot_overwrite_newer_settlement(db_session, add_account):
    add_account(1)

    older = sync_files_from_drives(
        db_session, list_files_fn=lambda a: [], run_id="older-run"
    )
    begin_sync("newer-run", [1])

    assert finish_sync(older) is False
    assert get_latest_settlement()["run_id"] == "newer-run"
    assert get_latest_settlement()["status"] == "running"
