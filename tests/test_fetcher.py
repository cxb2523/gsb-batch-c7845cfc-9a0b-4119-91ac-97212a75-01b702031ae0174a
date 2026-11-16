"""Tests for the retry / rate-limit behavior of fetch_with_retry."""

import asyncio
import logging

import httpx
import pytest

from tick_vault.config import CONFIG
from tick_vault.fetcher import RetriesExhaustedError, fetch_with_retry

URL = "https://datafeed.dukascopy.com/datafeed/TEST/2024/00/01/00h_ticks.bi5"


@pytest.fixture
def recorded_sleeps(monkeypatch):
    """Replace asyncio.sleep with a recorder so tests run instantly."""
    calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        calls.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return calls


def make_client(responses: list[httpx.Response]) -> httpx.AsyncClient:
    """Build an AsyncClient that returns the queued responses in order."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        assert queue, "More requests than mocked responses"
        return queue.pop(0)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_rate_limit_then_success(recorded_sleeps, monkeypatch):
    """A 429 with Retry-After is honored and the retry succeeds."""
    monkeypatch.setattr(CONFIG, "fetch_max_retry_attempts", 3)
    monkeypatch.setattr(CONFIG, "fetch_max_retry_wait", 300.0)

    client = make_client(
        [
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, content=b"tick-data"),
        ]
    )

    result = await fetch_with_retry(client, URL)

    assert result == b"tick-data"
    # Server-provided Retry-After is used as the wait time
    assert recorded_sleeps == [3.0]


async def test_retry_after_above_cap_is_truncated(recorded_sleeps, monkeypatch, caplog):
    """A Retry-After larger than fetch_max_retry_wait is capped and logged."""
    monkeypatch.setattr(CONFIG, "fetch_max_retry_attempts", 3)
    monkeypatch.setattr(CONFIG, "fetch_max_retry_wait", 10.0)

    client = make_client(
        [
            httpx.Response(429, headers={"Retry-After": "99999"}),
            httpx.Response(200, content=b"tick-data"),
        ]
    )

    with caplog.at_level(logging.WARNING, logger="tick_vault"):
        result = await fetch_with_retry(client, URL)

    assert result == b"tick-data"
    # Wait was truncated to the configured cap, not the server's 99999s
    assert recorded_sleeps == [10.0]
    assert any(
        "capping" in record.message.lower() for record in caplog.records
    ), "Expected a log record about capping the Retry-After wait"


async def test_retry_exhaustion_raises_and_does_not_sleep_forever(
    recorded_sleeps, monkeypatch
):
    """Persistent 429s raise RetriesExhaustedError after the configured attempts."""
    monkeypatch.setattr(CONFIG, "fetch_max_retry_attempts", 2)
    monkeypatch.setattr(CONFIG, "fetch_base_retry_delay", 1.0)
    monkeypatch.setattr(CONFIG, "fetch_max_retry_wait", 300.0)

    # 429 without Retry-After -> exponential backoff, then give up
    client = make_client([httpx.Response(429) for _ in range(3)])

    with pytest.raises(RetriesExhaustedError) as exc_info:
        await fetch_with_retry(client, URL)

    assert exc_info.value.url == URL
    # initial attempt + 2 retries => 2 backoff sleeps (1s, 2s)
    assert recorded_sleeps == [1.0, 2.0]


async def test_503_with_retry_after_uses_capped_server_delay(
    recorded_sleeps, monkeypatch
):
    """503 with Retry-After is treated as rate limiting and capped as well."""
    monkeypatch.setattr(CONFIG, "fetch_max_retry_attempts", 3)
    monkeypatch.setattr(CONFIG, "fetch_max_retry_wait", 5.0)

    client = make_client(
        [
            httpx.Response(503, headers={"Retry-After": "120"}),
            httpx.Response(200, content=b"tick-data"),
        ]
    )

    result = await fetch_with_retry(client, URL)

    assert result == b"tick-data"
    assert recorded_sleeps == [5.0]
