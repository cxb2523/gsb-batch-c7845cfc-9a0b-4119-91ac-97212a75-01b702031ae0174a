"""Tests for poll-cycle gating: a FAILED scrape must not advance
last_scrape_at nor write usage snapshots; OK and EMPTY must both do so.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.services.scheduler as sched
from app.database import Base
from app.models import Meter, Token, UsageSnapshot, User
from app.schemas import ScrapedToken
from app.scraper.kplc import ScrapResult, ScrapeStatus


@pytest.fixture
async def db_factory(monkeypatch):
    """In-memory SQLite wired into the scheduler's session factory."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sched, "async_session_factory", factory)
    yield factory
    await engine.dispose()


async def _make_meter(factory, last_scrape_at=None):
    async with factory() as db:
        user = User()
        db.add(user)
        await db.flush()
        meter = Meter(
            user_id=user.id,
            meter_number="12345678901",
            last_scrape_at=last_scrape_at,
        )
        db.add(meter)
        await db.commit()
        return meter.id


def _install_fake_scrape(monkeypatch, result: ScrapResult):
    async def fake_scrape(meter_number, account_number=None, session=None):
        return result
    monkeypatch.setattr(sched, "scrape_meter_tokens", fake_scrape)


async def _get_meter(factory, meter_id):
    async with factory() as db:
        return await db.get(Meter, meter_id)


async def _snapshot_count(factory, meter_id):
    async with factory() as db:
        res = await db.execute(
            select(func.count(UsageSnapshot.id)).where(UsageSnapshot.meter_id == meter_id)
        )
        return res.scalar() or 0


async def test_failed_scrape_does_not_advance_last_scrape_or_snapshot(db_factory, monkeypatch):
    old_ts = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    meter_id = await _make_meter(db_factory, last_scrape_at=old_ts)
    _install_fake_scrape(monkeypatch, ScrapResult(
        tokens=[], tariff=None, status=ScrapeStatus.FAILED,
        error="api: could not obtain KPLC bearer token; html_fallback: no usable HTML response",
    ))

    await sched.poll_all_meters()

    meter = await _get_meter(db_factory, meter_id)
    last = meter.last_scrape_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    assert last == old_ts  # untouched
    assert await _snapshot_count(db_factory, meter_id) == 0  # no snapshot written


async def test_empty_scrape_still_advances_last_scrape_and_writes_snapshot(db_factory, monkeypatch):
    old_ts = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    meter_id = await _make_meter(db_factory, last_scrape_at=old_ts)
    _install_fake_scrape(monkeypatch, ScrapResult(
        tokens=[], tariff=None, status=ScrapeStatus.EMPTY,
    ))

    await sched.poll_all_meters()

    meter = await _get_meter(db_factory, meter_id)
    last = meter.last_scrape_at
    assert last is not None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    assert last > old_ts  # advanced
    assert last > datetime.now(timezone.utc) - timedelta(minutes=5)
    assert await _snapshot_count(db_factory, meter_id) == 1


async def test_ok_scrape_inserts_tokens_with_query_path_source(db_factory, monkeypatch):
    meter_id = await _make_meter(db_factory)
    _install_fake_scrape(monkeypatch, ScrapResult(
        tokens=[
            ScrapedToken(token_number="1" * 20, units=10.0, amount=250.0,
                         payment_mode="M-PESA",
                         purchased_at=datetime(2026, 11, 10, 8, 0),
                         tariff="Domestic", source="api_serial"),
            ScrapedToken(token_number="2" * 20, units=5.0, amount=125.0,
                         payment_mode="M-PESA",
                         purchased_at=datetime(2026, 11, 12, 9, 30),
                         tariff="Domestic", source="api_account"),
        ],
        tariff="Domestic",
        status=ScrapeStatus.OK,
    ))

    await sched.poll_all_meters()

    async with db_factory() as db:
        res = await db.execute(
            select(Token).where(Token.meter_id == meter_id).order_by(Token.token_number)
        )
        tokens = list(res.scalars().all())
    assert [t.token_number for t in tokens] == ["1" * 20, "2" * 20]
    # Each row records which query path produced it
    assert tokens[0].source == "api_serial"
    assert tokens[1].source == "api_account"

    # Polling again with the same data must not create duplicate rows
    await sched.poll_all_meters()
    async with db_factory() as db:
        res = await db.execute(
            select(func.count(Token.id)).where(Token.meter_id == meter_id)
        )
        assert (res.scalar() or 0) == 2
