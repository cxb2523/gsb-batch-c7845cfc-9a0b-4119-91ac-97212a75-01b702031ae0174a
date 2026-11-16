"""Tests for the dashboard GET entry point: it must follow the same
three-state rule as the poller (failed scrape -> no last_scrape_at advance,
no snapshot) and expose scrape_ok / scrape_error to the frontend.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.routes.dashboard as dash_mod
from app.auth import get_current_user
from app.database import Base, get_db
from app.models import Meter, UsageSnapshot, User
from app.scraper.kplc import ScrapResult, ScrapeStatus


@pytest.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        user = User()
        db.add(user)
        await db.flush()
        meter = Meter(user_id=user.id, meter_number="12345678901")
        db.add(meter)
        await db.commit()
        user_id, meter_id = user.id, meter.id

    app = FastAPI()
    app.include_router(dash_mod.router)

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _current_user():
        async with factory() as session:
            return await session.get(User, user_id)

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = _current_user

    yield app, factory, meter_id
    await engine.dispose()


async def _set_last_scrape(factory, meter_id, value):
    async with factory() as db:
        meter = await db.get(Meter, meter_id)
        meter.last_scrape_at = value
        await db.commit()


async def _snapshot_count(factory, meter_id):
    async with factory() as db:
        res = await db.execute(
            select(func.count(UsageSnapshot.id)).where(UsageSnapshot.meter_id == meter_id)
        )
        return res.scalar() or 0


def _patch_poll(monkeypatch, result: ScrapResult, calls: list):
    async def fake_poll(meter, db, source):
        calls.append(source)
        return result
    monkeypatch.setattr(dash_mod, "_poll_single_meter", fake_poll)


async def test_dashboard_failed_scrape_marks_scrape_ok_false_and_freezes_state(env, monkeypatch):
    app, factory, meter_id = env
    old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    await _set_last_scrape(factory, meter_id, old_ts)

    calls = []
    _patch_poll(monkeypatch, ScrapResult(
        tokens=[], tariff=None, status=ScrapeStatus.FAILED,
        error="api: HTTP 500 from KPLC API; html_fallback: no usable HTML response",
    ), calls)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/dashboard")

    assert resp.status_code == 200
    body = resp.json()
    assert calls == ["page_refresh"]  # a scrape was attempted
    assert body["scrape_ok"] is False
    assert "HTTP 500" in body["scrape_error"]
    # last_scrape_at NOT advanced
    assert body["last_scrape_at"].replace("Z", "+00:00")[:19] == old_ts.isoformat()[:19]
    # no usage snapshot written
    assert await _snapshot_count(factory, meter_id) == 0


async def test_dashboard_empty_scrape_counts_as_success(env, monkeypatch):
    app, factory, meter_id = env
    old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    await _set_last_scrape(factory, meter_id, old_ts)

    calls = []
    _patch_poll(monkeypatch, ScrapResult(
        tokens=[], tariff=None, status=ScrapeStatus.EMPTY,
    ), calls)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/dashboard")

    assert resp.status_code == 200
    body = resp.json()
    assert body["scrape_ok"] is True
    assert body["scrape_error"] is None
    # last_scrape_at advanced to ~now
    new_ts = datetime.fromisoformat(body["last_scrape_at"].replace("Z", "+00:00"))
    assert new_ts > old_ts
    assert new_ts > datetime.now(timezone.utc) - timedelta(minutes=5)
    # snapshot written even though zero records came back
    assert await _snapshot_count(factory, meter_id) == 1


async def test_dashboard_fresh_data_skips_scrape_and_reports_ok(env, monkeypatch):
    app, factory, meter_id = env
    await _set_last_scrape(factory, meter_id, datetime.now(timezone.utc))

    calls = []
    _patch_poll(monkeypatch, ScrapResult(
        tokens=[], tariff=None, status=ScrapeStatus.FAILED, error="should not be called",
    ), calls)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/dashboard")

    assert resp.status_code == 200
    body = resp.json()
    assert calls == []  # data still fresh — no scrape attempted
    assert body["scrape_ok"] is True
    assert body["scrape_error"] is None


async def test_manual_refresh_failure_returns_scrape_error(env, monkeypatch):
    app, factory, meter_id = env
    old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    await _set_last_scrape(factory, meter_id, old_ts)

    calls = []
    _patch_poll(monkeypatch, ScrapResult(
        tokens=[], tariff=None, status=ScrapeStatus.FAILED,
        error="api: could not obtain KPLC bearer token",
    ), calls)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/dashboard/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["scrape_ok"] is False
    assert "bearer token" in body["scrape_error"]
    assert body["last_scrape_at"].replace("Z", "+00:00")[:19] == old_ts.isoformat()[:19]
    assert await _snapshot_count(factory, meter_id) == 0
