"""HTTP-level tests for the sync settlement endpoints.

These exercise the real FastAPI app and route wiring, but the background sync
job is stubbed so no Google credentials are needed.
"""

import pytest
from fastapi.testclient import TestClient

from services.auth_service import create_access_token
from services.sync_service import SyncSettlement, AccountSyncResult, finish_sync


@pytest.fixture()
def client(monkeypatch):
    import main as app_module
    from services import sync_service

    # The app's startup hook performs a sync.  Keep that a no-op so these tests
    # do not depend on credentials or the seeded config database.
    monkeypatch.setattr(app_module, "sync_files_from_drives", lambda *a, **kw: None)

    with TestClient(app_module.app) as test_client:
        # Startup registers a "running" settlement; clear it so assertions are
        # about the settlement the test publishes itself.
        monkeypatch.setattr(sync_service, "_latest_settlement", None)
        token = create_access_token({"sub": "dashboard"})
        test_client.cookies.set("access_token", token)
        yield test_client


def test_sync_returns_run_handle(client, monkeypatch):
    calls: list[str | None] = []

    def fake_job(run_id=None):
        calls.append(run_id)

    import routes.files as files_route
    monkeypatch.setattr(files_route, "run_sync_job", fake_job)

    response = client.post("/api/files/sync")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "running"
    assert body["run_id"]
    assert calls == [body["run_id"]]


def test_status_reports_per_account_results(client):
    settlement = SyncSettlement(
        run_id="api-run",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:05+00:00",
        accounts=[
            AccountSyncResult(account_index=1, success=True, file_count=3, created_count=2, updated_count=1),
            AccountSyncResult(account_index=2, success=False, error="RuntimeError: boom"),
        ],
    )
    finish_sync(settlement)

    response = client.get("/api/files/sync/status")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "api-run"
    assert body["status"] == "partial"
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1
    assert body["total_files"] == 3

    first, second = body["accounts"]
    assert first == {
        "account_index": 1,
        "email": None,
        "success": True,
        "file_count": 3,
        "created_count": 2,
        "updated_count": 1,
        "deleted_count": 0,
        "error": None,
    }
    assert second["success"] is False
    assert second["error"] == "RuntimeError: boom"


def test_status_requires_auth():
    from fastapi.testclient import TestClient as _TestClient
    import main as app_module

    with _TestClient(app_module.app) as anonymous:
        assert anonymous.get("/api/files/sync/status").status_code == 401
