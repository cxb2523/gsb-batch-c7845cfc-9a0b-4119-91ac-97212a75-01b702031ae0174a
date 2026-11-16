"""Tests for the incremental fetch behaviour of the two fetch modules.

The in-progress season is the only raw file that gains new matches (the
sources update it in place), so it must always be re-downloaded even with
``force=False``; completed seasons stay cached. ``force=True`` re-downloads
everything. Both ``src.fetch.football_data_csv`` and ``src.fetch.understat``
implement the same rule, so both are covered.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid

import pytest

import config
from src.fetch import football_data_csv as fd
from src.fetch import understat as us
from src.fetch import io as fetch_io
from src.fetch.io import SeasonStatus

FAKE_CSV = "I1,Date,HomeTeam,AwayTeam\nI1,2016-08-19,TeamA,TeamB\n"
FAKE_JSON = json.dumps({
    "dates": [
        {"isResult": False, "h": {"title": "TeamA"}, "a": {"title": "TeamB"},
         "datetime": "2026-08-15 18:30:00"},
    ]
})


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    """Redirect config.league_dir to a throwaway dir under the workspace.

    Under the workspace so ``out_path.relative_to(config.WORKSPACE)`` in the
    fetch modules' print statements keeps working.
    """
    base = config.TMP_DIR / "test_fetch"
    base.mkdir(parents=True, exist_ok=True)
    d = base / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "league_dir", lambda source, league: d / source / league)
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# football-data.co.uk
# ---------------------------------------------------------------------------

def _fd_path(raw_dir, season):
    return raw_dir / "football_data" / "serie_a" / f"{season}.csv"


def test_fd_completed_cached_skip(raw_dir, monkeypatch):
    season = "2016-17"  # completed
    out = _fd_path(raw_dir, season)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(FAKE_CSV, encoding="utf-8")
    calls = []
    monkeypatch.setattr(fd, "get_text", lambda url: calls.append(url) or FAKE_CSV)
    result = fd.fetch_season(season, league="serie_a", force=False)
    assert result == str(out)
    assert calls == []  # cached -> not re-downloaded


def test_fd_in_progress_refresh(raw_dir, monkeypatch):
    season = config.IN_PROGRESS_SEASON  # in progress
    out = _fd_path(raw_dir, season)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(FAKE_CSV, encoding="utf-8")
    calls = []
    monkeypatch.setattr(fd, "get_text", lambda url: calls.append(url) or FAKE_CSV)
    result = fd.fetch_season(season, league="serie_a", force=False)
    assert result == str(out)
    assert len(calls) == 1  # in progress -> re-downloaded


def test_fd_force_refresh(raw_dir, monkeypatch):
    season = "2016-17"  # completed, but force=True
    out = _fd_path(raw_dir, season)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(FAKE_CSV, encoding="utf-8")
    calls = []
    monkeypatch.setattr(fd, "get_text", lambda url: calls.append(url) or FAKE_CSV)
    result = fd.fetch_season(season, league="serie_a", force=True)
    assert result == str(out)
    assert len(calls) == 1  # force -> re-downloaded


def test_fd_404_returns_none(raw_dir, monkeypatch):
    season = "2016-17"

    def fake_get_text(url):
        raise FileNotFoundError("404 Not Found")

    monkeypatch.setattr(fd, "get_text", fake_get_text)
    result = fd.fetch_season(season, league="serie_a", force=True)
    assert result is None


# ---------------------------------------------------------------------------
# Understat
# ---------------------------------------------------------------------------

def _us_path(raw_dir, season):
    return raw_dir / "understat" / "serie_a" / f"{config.understat_year(season)}.json"


def test_us_completed_cached_skip(raw_dir, monkeypatch):
    season = "2016-17"  # completed
    out = _us_path(raw_dir, season)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(FAKE_JSON, encoding="utf-8")
    calls = []
    monkeypatch.setattr(us, "get_text", lambda url, **kw: calls.append(url) or FAKE_JSON)
    result = us.fetch_season(season, league="serie_a", force=False)
    assert result == str(out)
    assert calls == []  # cached -> not re-downloaded


def test_us_in_progress_refresh(raw_dir, monkeypatch):
    season = config.IN_PROGRESS_SEASON  # in progress
    out = _us_path(raw_dir, season)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(FAKE_JSON, encoding="utf-8")
    calls = []
    monkeypatch.setattr(us, "get_text", lambda url, **kw: calls.append(url) or FAKE_JSON)
    result = us.fetch_season(season, league="serie_a", force=False)
    assert result == str(out)
    assert len(calls) == 1  # in progress -> re-downloaded


def test_us_force_refresh(raw_dir, monkeypatch):
    season = "2016-17"  # completed, but force=True
    out = _us_path(raw_dir, season)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(FAKE_JSON, encoding="utf-8")
    calls = []
    monkeypatch.setattr(us, "get_text", lambda url, **kw: calls.append(url) or FAKE_JSON)
    result = us.fetch_season(season, league="serie_a", force=True)
    assert result == str(out)
    assert len(calls) == 1  # force -> re-downloaded


def test_us_404_returns_none(raw_dir, monkeypatch):
    season = "2016-17"

    def fake_get_text(url, **kw):
        raise FileNotFoundError("404 Not Found")

    monkeypatch.setattr(us, "get_text", fake_get_text)
    result = us.fetch_season(season, league="serie_a", force=True)
    assert result is None


# ---------------------------------------------------------------------------
# Resilient fetch_all: per-season settlement, continuation, atomic writes.
#
# These tests use a small throwaway league ("mini") with three seasons so the
# whole fetch_all loop runs quickly. Each entry point is driven through the
# same scenarios via a small per-source bundle.
# ---------------------------------------------------------------------------

MINI_SEASONS = ["2024-25", "2025-26", "2026-27"]
MINI_IN_PROGRESS = "2026-27"

MINI_CSV = "E0,Date,HomeTeam,AwayTeam\nE0,2024-08-17,TeamA,TeamB\n"
MINI_CSV_BAD = "X9,Date,HomeTeam,AwayTeam\nX9,2024-08-17,TeamA,TeamB\n"
MINI_JSON = json.dumps({"dates": [
    {"isResult": True, "h": {"title": "TeamA"}, "a": {"title": "TeamB"},
     "datetime": "2024-08-17 14:00:00"},
]})
MINI_JSON_NO_MATCHES = json.dumps({"teams": {}, "players": {}, "dates": []})


class _MiniBundle:
    """Per-source adapters so one test body covers both fetch entry points."""

    def __init__(self, module, source, valid_text, bad_text):
        self.module = module
        self.source = source
        self.valid_text = valid_text
        self.bad_text = bad_text

    def url_for(self, season):
        if self.source == "football_data":
            spec = config.LEAGUES["mini"]
            return config.FOOTBALL_DATA_URL.format(
                code=config.football_data_code(season), div=spec["fd_div"]
            )
        return (
            "https://understat.com/getLeagueData/"
            f"{config.LEAGUES['mini']['understat_slug']}/"
            f"{config.understat_year(season)}"
        )

    def path_for(self, raw_dir, season):
        if self.source == "football_data":
            return raw_dir / "football_data" / "mini" / f"{season}.csv"
        return (
            raw_dir / "understat" / "mini"
            / f"{config.understat_year(season)}.json"
        )

    def set_get_text(self, monkeypatch, handler):
        if self.source == "football_data":
            monkeypatch.setattr(self.module, "get_text", lambda url: handler(url))
        else:
            monkeypatch.setattr(
                self.module, "get_text", lambda url, **kw: handler(url)
            )


@pytest.fixture
def mini_league(monkeypatch, raw_dir):
    """Register a tiny 3-season league and disable inter-request sleeps."""
    monkeypatch.setitem(
        config.LEAGUES,
        "mini",
        {
            "name": "Mini League",
            "fd_div": "E0",
            "understat_slug": "EPL",
            "seasons": list(MINI_SEASONS),
            "in_progress": MINI_IN_PROGRESS,
            "expected_matches": 380,
            "pandemic_window": None,
        },
    )
    monkeypatch.setattr(fd, "polite_sleep", lambda: None)
    monkeypatch.setattr(us, "polite_sleep", lambda: None)
    return raw_dir


@pytest.fixture(params=[
    _MiniBundle(fd, "football_data", MINI_CSV, MINI_CSV_BAD),
    _MiniBundle(us, "understat", MINI_JSON, MINI_JSON_NO_MATCHES),
])
def bundle(request):
    return request.param


def _handler_by_season(bundle, responses):
    """Build a get_text stand-in keyed by the season encoded in the URL."""

    def handler(url):
        for season in MINI_SEASONS:
            if url == bundle.url_for(season):
                value = responses[season]
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected URL requested: {url}")

    return handler


def test_fetch_all_continues_after_failure_and_exits_nonzero(
    mini_league, monkeypatch, bundle
):
    # Middle season fails persistently; the others return valid payloads.
    responses = {
        MINI_SEASONS[0]: bundle.valid_text,
        MINI_SEASONS[1]: RuntimeError("persistent HTTP 500"),
        MINI_SEASONS[2]: bundle.valid_text,  # in-progress season
    }
    bundle.set_get_text(monkeypatch, _handler_by_season(bundle, responses))

    summary = bundle.module.fetch_all(league="mini", force=True)

    # Every season was still attempted despite the middle failure.
    assert [r.season for r in summary.results] == MINI_SEASONS
    statuses = {r.season: r.status for r in summary.results}
    assert statuses[MINI_SEASONS[0]] is SeasonStatus.SUCCESS
    assert statuses[MINI_SEASONS[1]] is SeasonStatus.FAILED
    assert "persistent HTTP 500" in summary.results[1].detail
    assert statuses[MINI_SEASONS[2]] is SeasonStatus.SUCCESS
    assert summary.count(SeasonStatus.FAILED) == 1
    assert [r.season for r in summary.failed] == [MINI_SEASONS[1]]

    # The failed season leaves no half-written target and no temp file.
    failed_path = bundle.path_for(mini_league, MINI_SEASONS[1])
    assert not failed_path.exists()
    assert list(failed_path.parent.glob("*.tmp")) == []
    # The successful seasons did land on disk.
    assert bundle.path_for(mini_league, MINI_SEASONS[0]).read_text(encoding="utf-8")
    assert bundle.path_for(mini_league, MINI_SEASONS[2]).read_text(encoding="utf-8")

    # CLI entry point must report the failure via a non-zero exit code.
    rc = bundle.module.main(["--league", "mini"])
    assert rc == 1


def test_failure_preserves_existing_cache(mini_league, monkeypatch, bundle):
    season = MINI_SEASONS[1]  # completed season with an old cache file
    cache_path = bundle.path_for(mini_league, season)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    old_content = "PREVIOUS-GOOD-CACHE"
    cache_path.write_text(old_content, encoding="utf-8")

    responses = {s: bundle.valid_text for s in MINI_SEASONS}
    responses[season] = RuntimeError("connection reset mid-download")
    bundle.set_get_text(monkeypatch, _handler_by_season(bundle, responses))

    summary = bundle.module.fetch_all(league="mini", force=True)
    result = next(r for r in summary.results if r.season == season)
    assert result.status is SeasonStatus.FAILED

    # Old cache is byte-for-byte intact; no half file, no stranded temp file.
    assert cache_path.read_text(encoding="utf-8") == old_content
    assert list(cache_path.parent.glob("*.tmp")) == []


def test_validation_failure_is_failed_and_keeps_old_cache(
    mini_league, monkeypatch, bundle
):
    season = MINI_SEASONS[0]
    cache_path = bundle.path_for(mini_league, season)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    old_content = "PREVIOUS-GOOD-CACHE"
    cache_path.write_text(old_content, encoding="utf-8")

    # Source responds, but the payload fails the published-content checks
    # (wrong Div code for CSV / no extractable matches for Understat).
    responses = {s: bundle.valid_text for s in MINI_SEASONS}
    responses[season] = bundle.bad_text
    bundle.set_get_text(monkeypatch, _handler_by_season(bundle, responses))

    summary = bundle.module.fetch_all(league="mini", force=True)
    result = next(r for r in summary.results if r.season == season)
    assert result.status is SeasonStatus.FAILED
    assert cache_path.read_text(encoding="utf-8") == old_content
    assert list(cache_path.parent.glob("*.tmp")) == []


def test_in_progress_season_is_refreshed(mini_league, monkeypatch, bundle):
    season = MINI_IN_PROGRESS
    cache_path = bundle.path_for(mini_league, season)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("STALE-IN-PROGRESS", encoding="utf-8")

    requested = []

    def handler(url):
        requested.append(url)
        return bundle.valid_text

    bundle.set_get_text(monkeypatch, handler)
    summary = bundle.module.fetch_all(league="mini", force=False)

    # The in-progress season was re-downloaded even though a cache exists...
    assert bundle.url_for(season) in requested
    in_progress = next(r for r in summary.results if r.season == season)
    assert in_progress.status is SeasonStatus.SUCCESS
    assert cache_path.read_text(encoding="utf-8") != "STALE-IN-PROGRESS"

    # ...and a second run refreshes it again (it is never treated as cached).
    requested.clear()
    summary2 = bundle.module.fetch_all(league="mini", force=False)
    assert bundle.url_for(season) in requested
    # The two completed seasons are skipped on the second run.
    assert summary2.count(SeasonStatus.SKIPPED) == 2


def test_404_is_not_published_not_failed(mini_league, monkeypatch, bundle):
    responses = {s: bundle.valid_text for s in MINI_SEASONS}
    responses[MINI_SEASONS[0]] = FileNotFoundError("404 Not Found")
    bundle.set_get_text(monkeypatch, _handler_by_season(bundle, responses))

    summary = bundle.module.fetch_all(league="mini", force=True)
    result = next(r for r in summary.results if r.season == MINI_SEASONS[0])
    assert result.status is SeasonStatus.NOT_FOUND
    assert summary.count(SeasonStatus.FAILED) == 0
    assert summary.failed == []
    # No cache file is created for an unpublished season.
    assert not bundle.path_for(mini_league, MINI_SEASONS[0]).exists()

    # All other seasons succeeded -> CLI exits zero.
    assert bundle.module.main(["--league", "mini"]) == 0


def test_summary_lists_every_season_with_totals(
    mini_league, monkeypatch, bundle, capsys
):
    responses = {
        MINI_SEASONS[0]: FileNotFoundError("404"),
        MINI_SEASONS[1]: RuntimeError("boom"),
        MINI_SEASONS[2]: bundle.valid_text,
    }
    bundle.set_get_text(monkeypatch, _handler_by_season(bundle, responses))
    bundle.module.fetch_all(league="mini", force=True)

    out = capsys.readouterr().out
    for season in MINI_SEASONS:
        assert season in out
    assert "total 3" in out
    assert "1 ok" in out
    assert "1 not published (404)" in out
    assert "1 failed" in out
    assert MINI_SEASONS[1] in out  # failed season named in the summary


def test_atomic_write_replaces_target_only_after_success(tmp_path):
    target = tmp_path / "2024-25.csv"
    target.write_text("old", encoding="utf-8")

    fetch_io.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert list(tmp_path.glob("*.tmp")) == []

    # A write that fails mid-stream must leave the old file and no temp file.
    class ExplodingWriter:
        def __init__(self, fd):
            self._fd = fd

        def write(self, _):
            raise OSError("disk full")

        def flush(self):
            raise OSError("disk full")

        def fileno(self):
            raise OSError("disk full")

        def close(self):
            # Release the real descriptor so the temp file can be unlinked
            # (Windows forbids deleting an open file).
            os.close(self._fd)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    real_fdopen = fetch_io.os.fdopen

    def exploding_fdopen(fd, *args, **kwargs):
        return ExplodingWriter(fd)

    fetch_io.os.fdopen = exploding_fdopen
    try:
        with pytest.raises(OSError):
            fetch_io.atomic_write_text(target, "partial")
    finally:
        fetch_io.os.fdopen = real_fdopen
    assert target.read_text(encoding="utf-8") == "new"
    assert list(tmp_path.glob("*.tmp")) == []
