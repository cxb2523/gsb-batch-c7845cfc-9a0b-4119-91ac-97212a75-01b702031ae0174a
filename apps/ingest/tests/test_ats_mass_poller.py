"""Tests for the ATS mass poller per-board outcome reporting.

Covers:
1. Mixed multi-platform happy path (matched vs. legit no_match boards).
2. A single board timing out / returning malformed JSON / HTTP errors does
   not affect other boards, and failures are distinguishable from empty
   results in the round summary.
3. Duplicate slugs are requested only once per round.
4. A platform-level task exception preserves the other platforms' results.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from src.radar.sources import ats_mass_poller as mp


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _FakeClient:
    """Routes requests by URL; records every requested URL."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self._routes = routes
        self.calls: list[str] = []

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        return await self._handle(url)

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        return await self._handle(url)

    async def _handle(self, url: str) -> _FakeResponse:
        self.calls.append(url)
        handler = self._routes[url]
        if callable(handler):
            return await handler()
        return handler


@pytest.fixture
def fake_client_factory(monkeypatch: pytest.MonkeyPatch):
    """Patch get_client; returns a setter installing a routed _FakeClient."""

    def _install(routes: dict[str, Any]) -> _FakeClient:
        client = _FakeClient(routes)

        async def _get_client(*args: Any, **kwargs: Any) -> _FakeClient:
            return client

        monkeypatch.setattr(mp, "get_client", _get_client)
        return client

    return _install


@pytest.fixture
def seed_slugs(monkeypatch: pytest.MonkeyPatch):
    """Replace the global slug store for one test (no GitHub harvest)."""

    def _seed(store: dict[str, set[str]]) -> None:
        monkeypatch.setattr(
            mp,
            "_SLUG_STORE",
            {"greenhouse": set(), "lever": set(), "ashby": set(), **store},
        )

    return _seed


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _gh_url(slug: str) -> str:
    return mp.GREENHOUSE_API.format(slug=slug)


def _lever_url(slug: str) -> str:
    return mp.LEVER_API.format(slug=slug)


def _ashby_url(slug: str) -> str:
    return mp.ASHBY_API.format(slug=slug)


async def test_mixed_platforms_happy_path(fake_client_factory, seed_slugs) -> None:
    """All three platforms return valid payloads; matched vs no_match split."""
    now = _now_iso()
    seed_slugs(
        {
            "greenhouse": {"acme", "emptyco"},
            "lever": {"betaco"},
            "ashby": {"gammaco"},
        }
    )
    fake_client_factory(
        {
            # One fresh intern job + one stale job + one senior title: only
            # the fresh intern job may survive the filters.
            _gh_url("acme"): _FakeResponse(
                payload={
                    "jobs": [
                        {
                            "title": "Software Engineer Intern",
                            "updated_at": now,
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                            "location": {"name": "NYC"},
                        },
                        {
                            "title": "Intern, 2024 (old)",
                            "updated_at": "2020-01-01T00:00:00Z",
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
                        },
                        {
                            "title": "Senior Staff Engineer",
                            "updated_at": now,
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/3",
                        },
                    ]
                }
            ),
            _gh_url("emptyco"): _FakeResponse(payload={"jobs": []}),
            _lever_url("betaco"): _FakeResponse(
                payload=[
                    {
                        "text": "New Grad Software Engineer",
                        "createdAt": now,
                        "hostedUrl": "https://jobs.lever.co/betaco/1",
                        "categories": {"location": "SF"},
                    }
                ]
            ),
            _ashby_url("gammaco"): _FakeResponse(
                payload={
                    "jobs": [
                        {
                            "title": "Junior Data Analyst",
                            "publishedAt": now,
                            "id": "abc123",
                            "locationName": "Remote",
                        }
                    ]
                }
            ),
        }
    )

    observations, summary = await mp.poll_all_mass_slugs()

    assert len(observations) == 3
    assert {o.extra["ats_platform"] for o in observations} == {"greenhouse", "lever", "ashby"}
    assert summary.total_boards == 4
    assert summary.outcomes == {mp.OUTCOME_MATCHED: 3, mp.OUTCOME_NO_MATCH: 1}
    assert summary.failures == []
    assert summary.platform_errors == []
    assert summary.jobs_found == 3
    assert summary.elapsed_s >= 0
    # no_match is a legitimate empty board, not a failure.
    no_match = [r for r in summary.board_results if r.outcome == mp.OUTCOME_NO_MATCH]
    assert [(r.platform, r.slug) for r in no_match] == [("greenhouse", "emptyco")]
    assert all(r.duration_s >= 0 for r in summary.board_results)


async def test_timeout_bad_json_and_http_error_do_not_break_others(
    fake_client_factory, seed_slugs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing boards are isolated and land in the failure list with reasons."""
    monkeypatch.setattr(mp, "MASS_POLL_REQUEST_TIMEOUT", 0.2)
    now = _now_iso()
    seed_slugs({"greenhouse": {"slowco", "badjson", "ghostco", "goodco"}})

    async def _slow() -> _FakeResponse:
        await asyncio.sleep(30)
        return _FakeResponse(payload={"jobs": []})

    fake_client_factory(
        {
            _gh_url("slowco"): _slow,
            _gh_url("badjson"): _FakeResponse(
                payload=None, json_error=ValueError("Expecting value: line 1 column 1")
            ),
            _gh_url("ghostco"): _FakeResponse(status_code=404, payload=None),
            _gh_url("goodco"): _FakeResponse(
                payload={
                    "jobs": [
                        {
                            "title": "Frontend Intern",
                            "updated_at": now,
                            "absolute_url": "https://boards.greenhouse.io/goodco/jobs/9",
                        }
                    ]
                }
            ),
        }
    )

    observations, summary = await mp.poll_all_mass_slugs()

    # The healthy board still produced its job.
    assert len(observations) == 1
    assert observations[0].extra["company_slug"] == "goodco"

    assert summary.outcomes == {
        mp.OUTCOME_MATCHED: 1,
        mp.OUTCOME_TIMEOUT: 1,
        mp.OUTCOME_INVALID_RESPONSE: 1,
        mp.OUTCOME_HTTP_ERROR: 1,
    }
    failures = {(f.platform, f.slug): f for f in summary.failures}
    assert set(failures) == {
        ("greenhouse", "slowco"),
        ("greenhouse", "badjson"),
        ("greenhouse", "ghostco"),
    }
    assert failures[("greenhouse", "slowco")].outcome == mp.OUTCOME_TIMEOUT
    assert "hard cap" in failures[("greenhouse", "slowco")].reason
    assert failures[("greenhouse", "badjson")].outcome == mp.OUTCOME_INVALID_RESPONSE
    assert failures[("greenhouse", "ghostco")].outcome == mp.OUTCOME_HTTP_ERROR
    assert "404" in failures[("greenhouse", "ghostco")].reason
    # Failures are never reported as empty boards.
    assert all(f.outcome != mp.OUTCOME_NO_MATCH for f in summary.failures)


async def test_invalid_structure_counts_as_invalid_response(
    fake_client_factory, seed_slugs
) -> None:
    """A 200 payload with the wrong shape is invalid_response, not no_match."""
    seed_slugs({"lever": {"weirdco"}, "ashby": {"oddco"}})
    fake_client_factory(
        {
            _lever_url("weirdco"): _FakeResponse(payload={"unexpected": "object"}),
            _ashby_url("oddco"): _FakeResponse(payload=["not", "an", "object"]),
        }
    )

    _, summary = await mp.poll_all_mass_slugs()

    assert summary.outcomes == {mp.OUTCOME_INVALID_RESPONSE: 2}
    assert {(f.platform, f.slug) for f in summary.failures} == {
        ("lever", "weirdco"),
        ("ashby", "oddco"),
    }


async def test_duplicate_slug_requested_once_per_round(fake_client_factory) -> None:
    """Case/whitespace slug variants collapse to a single request."""
    client = fake_client_factory({_gh_url("acme"): _FakeResponse(payload={"jobs": []})})

    jobs, results = await mp._poll_greenhouse_slugs(["Acme", "acme", " ACME ", "acme", ""])

    assert jobs == []
    assert client.calls == [_gh_url("acme")]
    assert len(results) == 1
    assert results[0].slug == "acme"
    assert results[0].outcome == mp.OUTCOME_NO_MATCH


async def test_platform_task_exception_preserves_other_platforms(
    fake_client_factory, seed_slugs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If one platform's gather blows up, the others still count."""
    now = _now_iso()
    seed_slugs({"greenhouse": {"acme"}, "lever": {"betaco"}})
    fake_client_factory(
        {
            _gh_url("acme"): _FakeResponse(
                payload={
                    "jobs": [
                        {
                            "title": "Backend Intern",
                            "updated_at": now,
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/7",
                        }
                    ]
                }
            )
        }
    )

    async def _explode(slugs: Any) -> Any:
        raise RuntimeError("lever scheduler blew up")

    monkeypatch.setattr(mp, "_poll_lever_slugs", _explode)

    observations, summary = await mp.poll_all_mass_slugs()

    assert len(observations) == 1
    assert observations[0].extra["ats_platform"] == "greenhouse"
    assert summary.outcomes == {mp.OUTCOME_MATCHED: 1}
    assert len(summary.platform_errors) == 1
    assert "lever" in summary.platform_errors[0]
    assert "lever scheduler blew up" in summary.platform_errors[0]
