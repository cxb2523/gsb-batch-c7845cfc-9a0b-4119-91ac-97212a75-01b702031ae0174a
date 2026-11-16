"""
Tests for per-record settlement in the congressional trade scraper.

Each trade is committed independently, so one bad record must not roll back
trades that were already persisted earlier in the same batch. These tests
exercise a batch containing a normal trade, a duplicate, a record that raises
during the database write, and another normal trade after the failure.
"""

import contextlib
import importlib.util
import logging
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.congressional_trade import CongressionalTrade
from app.services.congressional_scraper import CongressionalScraperService

CRON_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "cron_scrape.py"


def _load_cron_module():
    """Load scripts/cron_scrape.py without requiring it to be a package module."""
    spec = importlib.util.spec_from_file_location("cron_scrape_under_test", CRON_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubAPIClient:
    """API client stub returning a fixed list of trades (or raising)."""

    def __init__(self, trades=None, error=None):
        self._trades = trades or []
        self._error = error

    async def fetch_congressional_trades(self, symbol=None, from_date=None, to_date=None):
        if self._error:
            raise self._error
        return list(self._trades)


def _trade(
    name: str,
    ticker: str,
    transaction_type: str = "BUY",
    transaction_date: str = "2026-10-01",
) -> dict:
    return {
        "name": name,
        "chamber": "HOUSE",
        "ticker": ticker,
        "asset_description": f"{ticker} Common Stock",
        "transaction_date": transaction_date,
        "disclosure_date": "2026-10-05",
        "transaction_type": transaction_type,
        "amount_min": 1001.0,
        "amount_max": 15000.0,
        "amount_estimated": 8000.5,
        "owner_type": "Self",
        "asset_type": "Stock",
        "source": "test",
    }


@pytest.mark.asyncio
async def test_batch_settles_each_trade_independently(test_db: AsyncSession):
    """
    Normal and duplicate records settle correctly, the bad record lands in the
    failure list with a locatable identifier, and the committed trade survives.
    """
    good_before = _trade("Rep. Alice Smith", "AAPL")
    duplicate = _trade("Rep. Alice Smith", "AAPL")
    # Violates the check constraint on transaction_type -> fails on commit.
    bad = _trade("Rep. Bob Jones", "TSLA", transaction_type="HOLD")
    good_after = _trade("Rep. Carol White", "MSFT")

    api_client = _StubAPIClient(
        trades=[good_before, duplicate, bad, good_after]
    )
    scraper = CongressionalScraperService(api_client=api_client)

    result = await scraper.scrape_congressional_trades(db=test_db, days_back=60)

    # Backward-compatible fields remain present.
    assert result["success"] is True
    assert result["trades_processed"] == 3
    assert result["trades_created"] == 2
    assert result["trades_updated"] == 1

    # New settlement fields.
    assert result["trades_skipped"] == 1
    assert result["trades_failed"] == 1
    assert len(result["failed_trades"]) == 1
    assert result["errors"] and len(result["errors"]) == 1

    failure = result["failed_trades"][0]
    assert failure["index"] == 2
    assert failure["name"] == "Rep. Bob Jones"
    assert failure["ticker"] == "TSLA"
    assert failure["transaction_date"] == "2026-10-01"
    assert failure["reason"]

    # Trades committed before and after the failure must still be persisted.
    rows = (
        await test_db.execute(select(CongressionalTrade).order_by(CongressionalTrade.id))
    ).scalars().all()
    tickers = {row.ticker for row in rows}
    assert tickers == {"AAPL", "MSFT"}
    assert all(row.transaction_type == "BUY" for row in rows)


@pytest.mark.asyncio
async def test_duplicate_of_preexisting_trade_is_skipped_not_failed(test_db: AsyncSession):
    """A trade already in the database is skipped by dedup, not counted as failed."""
    existing = _trade("Rep. Alice Smith", "AAPL")
    scraper = CongressionalScraperService(api_client=_StubAPIClient(trades=[existing]))
    await scraper.scrape_congressional_trades(db=test_db, days_back=60)

    result = await scraper.scrape_congressional_trades(db=test_db, days_back=60)

    assert result["success"] is True
    assert result["trades_created"] == 0
    assert result["trades_processed"] == 1
    assert result["trades_skipped"] == 1
    assert result["trades_failed"] == 0
    assert result["failed_trades"] == []

    count = (await test_db.execute(select(CongressionalTrade))).scalars().all()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_fetch_failure_still_exposes_settlement_fields(test_db: AsyncSession):
    """An API-level failure keeps the original fields and adds empty failure lists."""
    scraper = CongressionalScraperService(
        api_client=_StubAPIClient(error=RuntimeError("upstream down"))
    )

    result = await scraper.scrape_congressional_trades(db=test_db, days_back=60)

    assert result["success"] is False
    assert result["error"] == "upstream down"
    assert result["trades_processed"] == 0
    assert result["trades_created"] == 0
    assert result["trades_skipped"] == 0
    assert result["trades_failed"] == 0
    assert result["failed_trades"] == []


@pytest.mark.asyncio
async def test_cron_reports_partial_success_and_failure_list(
    test_db: AsyncSession, monkeypatch, caplog
):
    """
    The cron branch logs created/skipped/failed counts and reports partial
    success instead of raising when some records failed.
    """
    cron = _load_cron_module()

    good_before = _trade("Rep. Alice Smith", "AAPL")
    duplicate = _trade("Rep. Alice Smith", "AAPL")
    bad = _trade("Rep. Bob Jones", "TSLA", transaction_type="HOLD")

    # Feed the scraper a deterministic batch and the test DB session.
    monkeypatch.setattr(
        "app.services.congressional_scraper.CongressionalAPIClient",
        lambda: _StubAPIClient(trades=[good_before, duplicate, bad]),
    )

    @contextlib.asynccontextmanager
    async def _test_session():
        # Deliberately does not close the fixture session.
        yield test_db

    monkeypatch.setattr("app.database.db_manager.get_session", _test_session)

    with caplog.at_level(logging.INFO, logger=cron.logger.name):
        result = await cron.scrape_congressional_trades(days_back=60)

    assert result["success"] is True, "failed records must not abort the batch"
    assert result["status"] == "partial_success"
    assert result["trades_processed"] == 2
    assert result["trades_created"] == 1
    assert result["trades_skipped"] == 1
    assert result["trades_failed"] == 1
    assert [entry["ticker"] for entry in result["failed_trades"]] == ["TSLA"]

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "created: 1" in log_text
    assert "skipped: 1" in log_text
    assert "failed: 1" in log_text
    assert "TSLA" in log_text

    # The good trade remains committed despite the later failing record.
    rows = (await test_db.execute(select(CongressionalTrade))).scalars().all()
    assert [row.ticker for row in rows] == ["AAPL"]


@pytest.mark.asyncio
async def test_cron_reports_full_success_without_failures(
    test_db: AsyncSession, monkeypatch
):
    """With no failures the cron branch reports a plain success."""
    cron = _load_cron_module()

    monkeypatch.setattr(
        "app.services.congressional_scraper.CongressionalAPIClient",
        lambda: _StubAPIClient(trades=[_trade("Rep. Alice Smith", "AAPL")]),
    )

    @contextlib.asynccontextmanager
    async def _test_session():
        yield test_db

    monkeypatch.setattr("app.database.db_manager.get_session", _test_session)

    result = await cron.scrape_congressional_trades(days_back=60)

    assert result["success"] is True
    assert result["status"] == "success"
    assert result["trades_failed"] == 0
    assert result["failed_trades"] == []
