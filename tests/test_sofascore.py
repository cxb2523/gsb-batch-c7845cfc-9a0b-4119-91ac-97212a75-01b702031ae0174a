"""Tests for esd.sofascore parsers, endpoints and types."""

import threading
import time

import pytest

from esd.exceptions import SourceBlocked, SourceError
from esd.sofascore.endpoints import SofascoreEndpoints
from esd.sofascore.service import SofascoreService
from esd.sofascore.types import (
    PlayerAttributes,
    StatusType,
    parse_event,
    parse_player,
    parse_top_players_match,
)
from esd.utils import fetch_many


def test_player_attributes_default_factory():
    first = PlayerAttributes()
    second = PlayerAttributes()
    assert isinstance(first.overview, list)
    assert first.overview == []
    first.overview.append("x")
    assert second.overview == []


def test_parse_player_handles_null_market_value_raw():
    player = parse_player(
        {
            "id": 10,
            "name": "Lionel Messi",
            "proposedMarketValue": None,
            "proposedMarketValueRaw": None,
        }
    )
    assert player.id == 10
    assert player.market_value == 0


def test_parse_top_players_match_handles_null_potm():
    top = parse_top_players_match(
        {
            "playerOfTheMatch": None,
            "bestHomeTeamPlayers": [{"player": {"id": 1, "name": "Home Star"}}],
            "bestAwayTeamPlayers": [],
        }
    )
    assert top.best is not None
    assert len(top.home_team) == 1
    assert top.home_team[0].name == "Home Star"


def test_parse_event_elapsed_minutes_when_not_started():
    event = parse_event(
        {
            "id": 100,
            "startTimestamp": None,
            "status": {"type": "notstarted", "description": "Not started"},
            "time": {},
        }
    )
    assert event.status.type == StatusType.NOT_STARTED
    assert event.total_elapsed_minutes == 0
    assert event.current_elapsed_minutes == 0


def test_search_endpoint_url_encodes_query():
    endpoints = SofascoreEndpoints()
    url = endpoints.search_endpoint("Real Madrid & Co", "all")
    assert "Real%20Madrid%20%26%20Co" in url


class _ConcurrencyTracker:
    """Counts how many threads are inside the tracked function at once."""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def __enter__(self):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def __exit__(self, *exc):
        with self._lock:
            self.active -= 1


def _blocked_api_json(url, proxies=None):
    raise SourceBlocked("blocked")


def test_browser_fallback_serialized_across_threads(monkeypatch):
    """Concurrent get_match_stats calls must not enter the browser together."""
    service = SofascoreService(use_playwright=True)
    tracker = _ConcurrencyTracker()

    def fake_playwright(url, *args, **kwargs):
        with tracker:
            time.sleep(0.05)
            if url.endswith("/statistics"):
                return {"statistics": [{"period": "ALL", "groups": []}]}
            return {"winProbability": {"home": 40}}

    monkeypatch.setattr("esd.sofascore.service.get_api_json", _blocked_api_json)
    monkeypatch.setattr(
        "esd.sofascore.service.get_json_playwright", fake_playwright
    )

    results = fetch_many(
        [lambda i=i: service.get_match_stats(1000 + i) for i in range(4)],
        max_workers=4,
    )

    assert len(results) == 4
    assert tracker.max_active == 1
    service.close()


def test_browser_empty_result_not_cached_and_retried(monkeypatch):
    """An empty browser result raises, is not cached, and is retried."""
    service = SofascoreService(use_playwright=True)
    calls = []

    def fake_playwright(url, *args, **kwargs):
        calls.append(url)
        # Fail empty the first time, succeed on the retry.
        return {} if len(calls) == 1 else {"event": {"id": 1}}

    monkeypatch.setattr("esd.sofascore.service.get_api_json", _blocked_api_json)
    monkeypatch.setattr(
        "esd.sofascore.service.get_json_playwright", fake_playwright
    )

    url = "https://api.sofascore.test/api/v1/event/1"
    with pytest.raises(SourceError):
        service._get_json(url)
    assert service._cache.get(url) is None

    data = service._get_json(url)
    assert data == {"event": {"id": 1}}
    assert calls == [url, url]
    service.close()


def test_non_browser_mode_fetches_concurrently(monkeypatch):
    """Without use_playwright, get_match_stats must keep fetching in parallel."""
    service = SofascoreService(use_playwright=False)
    tracker = _ConcurrencyTracker()

    def fake_api_json(url, proxies=None):
        with tracker:
            time.sleep(0.05)
            if url.endswith("/statistics"):
                return {"statistics": [{"period": "ALL", "groups": []}]}
            return {"winProbability": {"home": 40}}

    monkeypatch.setattr("esd.sofascore.service.get_api_json", fake_api_json)

    service.get_match_stats(1)
    assert tracker.max_active == 2


def test_get_json_with_source_reports_origin(monkeypatch):
    """Each fetch result reports whether curl_cffi or the browser produced it."""
    service = SofascoreService(use_playwright=True)

    monkeypatch.setattr(
        "esd.sofascore.service.get_api_json",
        lambda url, proxies=None: {"a": 1},
    )
    data, source = service._get_json_with_source("https://x.test/a")
    assert (data, source) == ({"a": 1}, "curl_cffi")

    monkeypatch.setattr("esd.sofascore.service.get_api_json", _blocked_api_json)
    monkeypatch.setattr(
        "esd.sofascore.service.get_json_playwright",
        lambda *args, **kwargs: {"b": 2},
    )
    data, source = service._get_json_with_source("https://x.test/b")
    assert (data, source) == ({"b": 2}, "playwright")

    # A cache hit still reports the source of the original fetch.
    data, source = service._get_json_with_source("https://x.test/b")
    assert (data, source) == ({"b": 2}, "playwright")
    service.close()


def test_blocked_without_playwright_propagates(monkeypatch):
    """Without the browser fallback, SourceBlocked reaches the caller."""
    service = SofascoreService(use_playwright=False)
    monkeypatch.setattr("esd.sofascore.service.get_api_json", _blocked_api_json)
    with pytest.raises(SourceBlocked):
        service._get_json("https://x.test/c")
