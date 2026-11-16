import json
import os

import pytest

import src.search as search_module
from src import DuckDuckGo
from src.duckduckgo_scraper import (
    FAILED_DUE_TO_CREDITS_EXHAUSTED,
    FAILED_DUE_TO_UNKNOWN_ERROR,
)
from src.search import do_request


def _page(results, next_link=None):
    return {
        "data": {
            "results": results,
            "next": next_link,
        },
        "error": None,
    }


def _result(name, rank):
    return {
        "rank": rank,
        "title": name,
        "url": f"https://example.com/{name}",
        "description": f"about {name}",
    }


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _search_dir(tmp_path):
    return tmp_path / "output"


def test_two_queries_one_success_one_credits_exhausted(workdir, monkeypatch):
    """A quota failure on one query must not discard the other query's data."""
    calls = []

    def fake_do_request(data, retry_count=3):
        link = data["params"]["link"]
        calls.append(link)
        if "query=mango" in link:
            return _page([_result("mango-1", 1), _result("mango-2", 2)])
        return {"data": None, "error": FAILED_DUE_TO_CREDITS_EXHAUSTED}

    monkeypatch.setattr(search_module, "do_request", fake_do_request)

    result = DuckDuckGo.search(["mango", "watermelon"], max=10, key="key", use_cache=False)

    # Return value stays list compatible.
    assert isinstance(result, list)
    assert [item["title"] for item in result] == ["mango-1", "mango-2"]

    summary = result.summary
    assert summary["successful_count"] == 1
    assert summary["partial_count"] == 0
    assert summary["failed_count"] == 1
    assert summary["successful"] == [{"query": "mango", "count": 2}]
    assert summary["partial"] == []
    assert summary["failed"] == [{"query": "watermelon", "error": FAILED_DUE_TO_CREDITS_EXHAUSTED}]
    assert result.failed_queries() == ["watermelon"]

    # Successful query is written as before, failed one gets no result file.
    assert (workdir / "output/mango/json/mango.json").exists()
    assert (workdir / "output/mango/csv/mango.csv").exists()
    assert not (workdir / "output/watermelon").exists()
    # Aggregated file only holds the data that was actually scraped.
    assert _read_json(workdir / "output/_all/json/_all.json") == [
        _result("mango-1", 1),
        _result("mango-2", 2),
    ]


def test_partial_second_page_unknown_error_keeps_first_page(workdir, monkeypatch):
    """A page-2 failure must keep page 1 and be reported as partial."""
    calls = []

    def fake_do_request(data, retry_count=3):
        link = data["params"]["link"]
        calls.append(link)
        if "offset=" in link:
            return {"data": None, "error": FAILED_DUE_TO_UNKNOWN_ERROR}
        return _page(
            [_result("apple-1", 1), _result("apple-2", 2)],
            next_link="https://duckduckgo-scraper.p.rapidapi.com/search/?query=apple&offset=10",
        )

    monkeypatch.setattr(search_module, "do_request", fake_do_request)

    result = DuckDuckGo.search("apple", max=10, key="key", use_cache=False)

    assert isinstance(result, list)
    assert len(result) == 2
    assert [item["title"] for item in result] == ["apple-1", "apple-2"]

    summary = result.summary
    assert summary["successful_count"] == 0
    assert summary["partial_count"] == 1
    assert summary["failed_count"] == 0
    assert summary["partial"] == [
        {"query": "apple", "count": 2, "error": FAILED_DUE_TO_UNKNOWN_ERROR}
    ]
    assert result.partial_queries() == ["apple"]
    assert result.successful_queries() == []
    assert result.failed_queries() == []

    # Partial results are still persisted like successes.
    assert _read_json(workdir / "output/apple/json/apple.json") == [
        _result("apple-1", 1),
        _result("apple-2", 2),
    ]
    assert _read_json(workdir / "output/_all/json/_all.json") == [
        _result("apple-1", 1),
        _result("apple-2", 2),
    ]
    # Two pages were attempted (first page + failing second page).
    assert len(calls) == 2


def test_all_queries_successful(workdir, monkeypatch):
    def fake_do_request(data, retry_count=3):
        query = "mango" if "query=mango" in data["params"]["link"] else "watermelon"
        return _page([_result(f"{query}-1", 1), _result(f"{query}-2", 2)])

    monkeypatch.setattr(search_module, "do_request", fake_do_request)

    result = DuckDuckGo.search(["mango", "watermelon"], max=10, key="key", use_cache=False)

    assert len(result) == 4
    summary = result.summary
    assert summary["successful_count"] == 2
    assert summary["partial_count"] == 0
    assert summary["failed_count"] == 0
    assert summary["successful"] == [
        {"query": "mango", "count": 2},
        {"query": "watermelon", "count": 2},
    ]
    assert summary["total_results"] == 4

    for query in ("mango", "watermelon"):
        assert (workdir / f"output/{query}/json/{query}.json").exists()
        assert (workdir / f"output/{query}/csv/{query}.csv").exists()
    assert len(_read_json(workdir / "output/_all/json/_all.json")) == 4


def test_partial_and_failed_results_are_not_cached(workdir, monkeypatch):
    """Failures and partial runs must not poison the cache for a later retry."""
    state = {"page2_fails": True}

    def fake_do_request(data, retry_count=3):
        link = data["params"]["link"]
        if "query=bad" in link:
            return {"data": None, "error": FAILED_DUE_TO_CREDITS_EXHAUSTED}
        if "offset=" in link:
            if state["page2_fails"]:
                return {"data": None, "error": FAILED_DUE_TO_UNKNOWN_ERROR}
            return _page([_result("good-2", 2)])
        return _page(
            [_result("good-1", 1)],
            next_link="https://duckduckgo-scraper.p.rapidapi.com/search/?query=good&offset=10",
        )

    monkeypatch.setattr(search_module, "do_request", fake_do_request)

    first = DuckDuckGo.search(["bad", "good"], max=10, key="key", use_cache=True)
    assert first.summary["partial"] == [
        {"query": "good", "count": 1, "error": FAILED_DUE_TO_UNKNOWN_ERROR}
    ]
    assert first.summary["failed"] == [
        {"query": "bad", "error": FAILED_DUE_TO_CREDITS_EXHAUSTED}
    ]

    cache_dir = workdir / "cache/search"
    cached_payloads = []
    if cache_dir.exists():
        for name in os.listdir(cache_dir):
            with open(cache_dir / name, encoding="utf-8") as handle:
                cached_payloads.append(json.load(handle))
    # Neither the failed nor the partial query may leave anything in the cache.
    assert cached_payloads == []

    # A later run with the failure fixed must hit the network again, not a
    # stale failure from the cache.
    state["page2_fails"] = False
    state["requests"] = 0

    def fake_do_request_ok(data, retry_count=3):
        state["requests"] += 1
        return _page([_result("recovered-1", 1)])

    monkeypatch.setattr(search_module, "do_request", fake_do_request_ok)
    recovered = DuckDuckGo.search(["bad", "good"], max=10, key="key", use_cache=True)

    # One request for the previously failed query, one for the re-fetched
    # partial query (its partial page 1 was never cached either).
    assert state["requests"] == 2
    assert recovered.summary["successful_count"] == 2
    assert len(recovered) == 2


def test_do_request_classifies_quota_and_retries_rate_limit(workdir, monkeypatch):
    """Verify do_request still classifies errors and retries rate limits while
    keeping the RapidAPI headers and pagination URL construction unchanged."""
    responses = [
        _FakeResponse(429, {"message": "You have exceeded the rate limit per second for your plan, BASIC, by making 6 API calls in the last 6 second(s)."}),
        _FakeResponse(200, {"results": [_result("mango-1", 1)], "next": None}),
        _FakeResponse(429, {"message": "You have exceeded the MONTHLY quota for Free plan on endpoint"}),
        _FakeResponse(401, {"message": "You are not subscribed to this API."}),
    ]
    seen = []

    def fake_get(url, headers=None, **kwargs):
        seen.append((url, headers))
        return responses.pop(0)

    monkeypatch.setattr(search_module.requests, "get", fake_get)
    monkeypatch.setattr(search_module, "sleep", lambda seconds: None)

    first = do_request({"key": "secret-key", "params": {"link": "https://example.test/?query=mango"}})
    assert first["error"] is None
    assert first["data"]["results"] == [_result("mango-1", 1)]
    # Rate limited call was retried once.
    assert len(seen) == 2
    for url, headers in seen:
        assert headers == {
            "X-RapidAPI-Key": "secret-key",
            "X-RapidAPI-Host": "duckduckgo-scraper.p.rapidapi.com",
        }

    quota = do_request({"key": "secret-key", "params": {"link": "https://example.test/?query=mango"}})
    assert quota["error"] == FAILED_DUE_TO_CREDITS_EXHAUSTED

    unsubscribed = do_request({"key": "secret-key", "params": {"link": "https://example.test/?query=mango"}})
    assert unsubscribed["error"] == "FAILED_DUE_TO_NOT_SUBSCRIBED"


def test_pagination_url_construction_is_unchanged(workdir, monkeypatch):
    """The `next` URL returned by the API is used verbatim, and the first page
    URL keeps the `query` parameter exactly as before."""
    urls = []

    def fake_do_request(data, retry_count=3):
        urls.append(data["params"]["link"])
        if len(urls) == 1:
            return _page(
                [_result("mango-1", 1)],
                next_link="https://duckduckgo-scraper.p.rapidapi.com/search/?query=mango&offset=10",
            )
        return _page([_result("mango-2", 2)])

    monkeypatch.setattr(search_module, "do_request", fake_do_request)

    DuckDuckGo.search("mango", max=10, key="key", use_cache=False)

    assert urls == [
        "https://duckduckgo-scraper.p.rapidapi.com/search/?query=mango",
        "https://duckduckgo-scraper.p.rapidapi.com/search/?query=mango&offset=10",
    ]


def test_max_is_respected_for_partial_results(workdir, monkeypatch):
    def fake_do_request(data, retry_count=3):
        if "offset=" in data["params"]["link"]:
            return {"data": None, "error": FAILED_DUE_TO_UNKNOWN_ERROR}
        return _page(
            [_result("mango-1", 1), _result("mango-2", 2)],
            next_link="https://duckduckgo-scraper.p.rapidapi.com/search/?query=mango&offset=10",
        )

    monkeypatch.setattr(search_module, "do_request", fake_do_request)

    result = DuckDuckGo.search("mango", max=3, key="key", use_cache=False)

    # Page 1 gave 2 of the 3 wanted results, page 2 failed: keep the 2.
    assert len(result) == 2
    assert result.summary["partial"] == [
        {"query": "mango", "count": 2, "error": FAILED_DUE_TO_UNKNOWN_ERROR}
    ]


def test_missing_key_is_reported_as_failure(workdir):
    result = DuckDuckGo.search(["mango"], max=10, key=None, use_cache=False)

    assert isinstance(result, list)
    assert len(result) == 0
    assert result.summary["failed"] == [{"query": "mango", "error": "FAILED_DUE_TO_NO_KEY"}]
    assert not (workdir / "output").exists()


def test_one_query_raising_does_not_abort_the_batch(workdir, monkeypatch):
    def fake_do_request(data, retry_count=3):
        if "query=broken" in data["params"]["link"]:
            raise RuntimeError("boom")
        return _page([_result("ok-1", 1)])

    monkeypatch.setattr(search_module, "do_request", fake_do_request)

    result = DuckDuckGo.search(["broken", "ok"], max=10, key="key", use_cache=False)

    assert [item["title"] for item in result] == ["ok-1"]
    summary = result.summary
    assert summary["successful"] == [{"query": "ok", "count": 1}]
    assert summary["failed"] == [{"query": "broken", "error": FAILED_DUE_TO_UNKNOWN_ERROR}]
    assert (workdir / "output/ok/json/ok.json").exists()
    assert not (workdir / "output/broken").exists()


def test_summary_is_accessible_by_attribute_and_key(workdir, monkeypatch):
    monkeypatch.setattr(
        search_module,
        "do_request",
        lambda data, retry_count=3: _page([_result("mango-1", 1)]),
    )

    result = DuckDuckGo.search("mango", max=10, key="key", use_cache=False)

    assert result.summary is result["summary"]
    assert result.to_dict()["successful_count"] == 1
    assert result.successful_queries() == ["mango"]
    # Plain list semantics are unaffected.
    assert len(result) == 1 and result[0]["title"] == "mango-1"
    assert result[:] == [result[0]]


def test_partial_pagination_end_to_end_with_fake_http(workdir, monkeypatch):
    """Same partial scenario as above, but faking `requests.get` instead of
    `do_request`, so headers/caching/URL construction run for real."""
    urls = []

    def fake_get(url, headers=None, **kwargs):
        urls.append(url)
        if "offset=" in url:
            return _FakeResponse(500, {"message": "Internal failure"})
        return _FakeResponse(
            200,
            {
                "results": [_result("apple-1", 1), _result("apple-2", 2)],
                "next": "https://duckduckgo-scraper.p.rapidapi.com/search/?query=apple&offset=10",
            },
        )

    monkeypatch.setattr(search_module.requests, "get", fake_get)

    result = DuckDuckGo.search("apple", max=10, key="secret", use_cache=True)

    assert len(result) == 2
    assert result.summary["partial"] == [
        {"query": "apple", "count": 2, "error": FAILED_DUE_TO_UNKNOWN_ERROR}
    ]
    assert urls == [
        "https://duckduckgo-scraper.p.rapidapi.com/search/?query=apple",
        "https://duckduckgo-scraper.p.rapidapi.com/search/?query=apple&offset=10",
    ]
    assert _read_json(workdir / "output/apple/json/apple.json") == [
        _result("apple-1", 1),
        _result("apple-2", 2),
    ]
    # A partial run is never cached, even though it did write output files.
    cache_dir = workdir / "cache/search"
    assert not cache_dir.exists() or os.listdir(cache_dir) == []


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload
