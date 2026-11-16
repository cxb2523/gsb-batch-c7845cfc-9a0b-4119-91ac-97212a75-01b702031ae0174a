"""Tests for download_range batch behavior when individual chunks fail."""

import logging
from datetime import UTC, datetime, timedelta

from tick_vault.chunk import TickChunk
from tick_vault.config import CONFIG
from tick_vault.downloader import download_range
from tick_vault.fetcher import RetriesExhaustedError
from tick_vault.metadata import MetadataDB

SYMBOL = "TESTUSD"
START = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
HOURS = 4
END = START + timedelta(hours=HOURS)


def _chunk(hour_offset: int) -> TickChunk:
    return TickChunk(symbol=SYMBOL, time=START + timedelta(hours=hour_offset))


async def test_exhausted_chunk_fails_but_batch_completes_and_is_refetched(
    isolated_config, monkeypatch, caplog
):
    """
    When one chunk exhausts its retries:
    - the other chunks still complete and are written to metadata,
    - the failed chunk is NOT recorded as attempted,
    - a rerun of the same range fetches only the failed chunk.
    """
    fail_chunk = _chunk(1)
    empty_chunk = _chunk(2)  # confirmed 404 -> must still be recorded

    async def fake_fetch(client, url):
        if url == fail_chunk.url:
            raise RetriesExhaustedError("max retries exceeded", url=url)
        if url == empty_chunk.url:
            return None  # 404: no data for this hour
        return b"\x00\x01\x02\x03"

    monkeypatch.setattr("tick_vault.download_worker.fetch_with_retry", fake_fetch)

    with caplog.at_level(logging.WARNING, logger="tick_vault"):
        await download_range(SYMBOL, START, END)

    # Successful chunks are on disk
    assert _chunk(0).path().exists()
    assert _chunk(3).path().exists()
    assert not fail_chunk.path().exists()
    assert not empty_chunk.path().exists()

    with MetadataDB(CONFIG.metadata_db_path) as db:
        # Only the failed chunk remains "not attempted"
        remaining = db.find_not_attempted_chunks(SYMBOL, START, END)
        assert [c.time for c in remaining] == [fail_chunk.time]

        # Successful chunks recorded with data; the 404 chunk recorded without
        available = {c.time for c in db.get_available_chunks(SYMBOL, START, END)}
        assert available == {_chunk(0).time, _chunk(3).time}

    # Failure is reported in a readable summary (count + details)
    failure_logs = [r.message for r in caplog.records if "failed" in r.message.lower()]
    assert any("1 chunk(s) failed" in msg for msg in failure_logs)
    assert any(fail_chunk.time.isoformat() in msg for msg in failure_logs)

    # Rerun the same range: only the failed chunk is fetched again
    fetched_urls: list[str] = []

    async def fake_fetch_success(client, url):
        fetched_urls.append(url)
        return b"\x09\x08\x07"

    monkeypatch.setattr(
        "tick_vault.download_worker.fetch_with_retry", fake_fetch_success
    )

    await download_range(SYMBOL, START, END)

    assert fetched_urls == [fail_chunk.url]
    assert fail_chunk.path().exists()

    with MetadataDB(CONFIG.metadata_db_path) as db:
        assert db.find_not_attempted_chunks(SYMBOL, START, END) == []
        available = {c.time for c in db.get_available_chunks(SYMBOL, START, END)}
        assert available == {
            _chunk(0).time,
            _chunk(1).time,
            _chunk(3).time,
        }


async def test_all_chunks_succeed_writes_full_metadata(isolated_config, monkeypatch):
    """Baseline: with no failures every chunk (incl. 404s) is recorded."""

    async def fake_fetch(client, url):
        return b"\x01\x02"

    monkeypatch.setattr("tick_vault.download_worker.fetch_with_retry", fake_fetch)

    await download_range(SYMBOL, START, END)

    with MetadataDB(CONFIG.metadata_db_path) as db:
        assert db.find_not_attempted_chunks(SYMBOL, START, END) == []
        assert len(db.get_available_chunks(SYMBOL, START, END)) == HOURS
