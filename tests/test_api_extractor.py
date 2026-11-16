"""Tests for paginated, retryable, partial-failure-safe API extraction."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from etl.pipeline import ETLPipeline
from extractors.api_extractor import APIExtractor, PartialExtractionError

# ---------------------------------------------------------------------------
# Stub HTTP machinery
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            http_error = requests.HTTPError(f"HTTP {self.status_code}")
            http_error.response = self
            raise http_error

    def json(self):
        return self._payload


class FakeSession:
    """In-memory replacement for requests.Session driven by a handler callable."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(
            {"url": url, "params": dict(params or {}), "timeout": timeout}
        )
        return self.handler(dict(params or {}))


def page_payload(page, size=2):
    return {
        "data": {"items": [{"id": page * 100 + i, "page": page} for i in range(size)]}
    }


def make_page_handler(pages, failures=None):
    """Serve page payloads; ``failures`` maps page number -> FakeResponse."""
    failures = failures or {}

    def handler(params):
        page = params["page"]
        if page in failures:
            return failures[page]
        return FakeResponse(payload=pages.get(page, {"data": {"items": []}}))

    return handler


# ---------------------------------------------------------------------------
# Backwards compatibility: no pagination configured -> single request
# ---------------------------------------------------------------------------


def test_single_request_behaviour_without_pagination():
    session = FakeSession(lambda params: FakeResponse(payload=[{"id": 1}, {"id": 2}]))

    result = APIExtractor(
        "https://api.example.com/things",
        params={"token": "abc"},
        records_path=None,
        session=session,
    ).extract()

    assert result.ok
    assert result.stop_reason == "single_request"
    assert len(session.calls) == 1
    assert session.calls[0]["params"] == {"token": "abc"}
    assert list(result.frame["id"]) == [1, 2]


def test_records_path_applies_in_single_request():
    session = FakeSession(lambda params: FakeResponse(payload=page_payload(1, size=3)))
    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        session=session,
    ).extract()

    assert len(result.frame) == 3
    assert set(result.frame["page"]) == {1}


# ---------------------------------------------------------------------------
# Happy path: normal multi-page responses are merged
# ---------------------------------------------------------------------------


def test_pagination_merges_all_pages_until_empty_page():
    pages = {1: page_payload(1), 2: page_payload(2), 3: {"data": {"items": []}}}
    session = FakeSession(make_page_handler(pages))

    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        pagination={"page_param": "page", "start_page": 1, "max_pages": 10},
        session=session,
    ).extract()

    assert result.ok
    assert result.status == "success"
    assert result.stop_reason == "empty_page"
    assert result.pages_fetched == [1, 2]
    assert len(result.frame) == 4
    assert list(result.frame["id"]) == [100, 101, 200, 201]
    # Empty page was requested to detect the end; nothing after it.
    assert [call["params"]["page"] for call in session.calls] == [1, 2, 3]


def test_max_pages_caps_the_number_of_requests():
    pages = {page: page_payload(page) for page in range(1, 6)}
    session = FakeSession(make_page_handler(pages))

    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        pagination={"page_param": "page", "max_pages": 3},
        session=session,
    ).extract()

    assert result.stop_reason == "max_pages"
    assert result.pages_fetched == [1, 2, 3]
    assert len(result.frame) == 6
    assert len(session.calls) == 3


def test_start_page_and_page_size_are_passed_as_configured():
    pages = {
        5: {"data": {"items": [{"v": i} for i in range(50)]}},
        6: {"data": {"items": []}},
    }
    session = FakeSession(
        lambda params: FakeResponse(
            payload=pages.get(params["p"], {"data": {"items": []}})
        )
    )

    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        pagination={
            "page_param": "p",
            "start_page": 5,
            "page_size": 50,
            "page_size_param": "size",
            "max_pages": 10,
        },
        session=session,
    ).extract()

    assert result.pages_fetched == [5]
    first_call = session.calls[0]["params"]
    assert first_call == {"p": 5, "size": 50}


def test_records_path_is_applied_to_every_page():
    pages = {
        1: {"data": {"items": [{"v": "a"}]}},
        2: {"data": {"items": [{"v": "b"}]}},
        3: {"data": {"items": []}},
    }
    session = FakeSession(make_page_handler(pages))

    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        pagination={"page_param": "page"},
        session=session,
    ).extract()

    assert list(result.frame["v"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# Empty page: stop early, never request beyond the boundary
# ---------------------------------------------------------------------------


def test_empty_first_page_makes_no_further_requests():
    session = FakeSession(make_page_handler({1: {"data": {"items": []}}}))

    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        pagination={"page_param": "page", "max_pages": 10},
        session=session,
    ).extract()

    assert result.ok
    assert result.stop_reason == "empty_page"
    assert result.pages_fetched == []
    assert len(session.calls) == 1
    assert result.frame.empty


# ---------------------------------------------------------------------------
# Failure path: retries, partial data preservation, interpretable outcome
# ---------------------------------------------------------------------------


def test_persistent_page_failure_keeps_fetched_pages_and_records_reason():
    pages = {1: page_payload(1), 2: page_payload(2), 3: page_payload(3)}
    failures = {3: FakeResponse(status_code=500)}
    session = FakeSession(make_page_handler(pages, failures))

    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        pagination={"page_param": "page", "max_pages": 10},
        retry={"max_retries": 2},
        session=session,
    ).extract()

    assert not result.ok
    assert result.status == "partial"
    assert result.stop_reason == "page_failure"
    assert result.pages_fetched == [1, 2]
    assert len(result.frame) == 4  # data from pages 1 and 2 preserved
    assert result.failed_pages == [
        {"page": 3, "error": result.failed_pages[0]["error"]}
    ]
    assert "HTTP 500" in result.failed_pages[0]["error"]
    # 1 initial attempt + 2 retries, all on page 3.
    assert [call["params"]["page"] for call in session.calls] == [1, 2, 3, 3, 3]


def test_failure_on_first_page_reports_status_failed_with_empty_frame():
    failures = {1: FakeResponse(status_code=503)}
    session = FakeSession(make_page_handler({}, failures))

    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        pagination={"page_param": "page"},
        retry={"max_retries": 1},
        session=session,
    ).extract()

    assert not result.ok
    assert result.status == "failed"
    assert result.frame.empty
    assert result.failed_pages[0]["page"] == 1


def test_retry_succeeds_after_transient_rate_limit():
    responses = iter(
        [
            FakeResponse(status_code=429),
            FakeResponse(status_code=429),
            FakeResponse(payload=page_payload(1)),
            FakeResponse(payload={"data": {"items": []}}),
        ]
    )
    session = FakeSession(lambda params: next(responses))

    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        pagination={"page_param": "page"},
        retry={"max_retries": 2},
        session=session,
    ).extract()

    assert result.ok
    assert result.pages_fetched == [1]
    assert [call["params"]["page"] for call in session.calls] == [1, 1, 1, 2]


def test_non_retryable_4xx_fails_immediately_without_retry():
    failures = {2: FakeResponse(status_code=404)}
    pages = {1: page_payload(1)}
    session = FakeSession(make_page_handler(pages, failures))

    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        pagination={"page_param": "page"},
        retry={"max_retries": 5},
        session=session,
    ).extract()

    assert not result.ok
    assert result.status == "partial"
    assert [call["params"]["page"] for call in session.calls] == [1, 2]
    assert "HTTP 404" in result.failed_pages[0]["error"]


def test_connection_error_is_retried_then_reported():
    def handler(params):
        raise requests.ConnectionError("connection reset")

    session = FakeSession(handler)
    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        pagination={"page_param": "page"},
        retry={"max_retries": 2},
        session=session,
    ).extract()

    assert result.status == "failed"
    assert len(session.calls) == 3
    assert "connection reset" in result.failed_pages[0]["error"]


def test_invalid_records_path_on_page_is_reported_as_page_failure():
    session = FakeSession(lambda params: FakeResponse(payload={"unexpected": []}))

    result = APIExtractor(
        "https://api.example.com/things",
        records_path="data.items",
        pagination={"page_param": "page"},
        session=session,
    ).extract()

    assert result.status == "failed"
    assert result.failed_pages[0]["page"] == 1
    assert "records_path" in result.failed_pages[0]["error"]


def test_pagination_requires_page_param():
    with pytest.raises(ValueError, match="page_param"):
        APIExtractor(
            "https://api.example.com/things",
            pagination={"max_pages": 5},
        )


# ---------------------------------------------------------------------------
# Pipeline integration: partial extraction ends with an interpretable error
# ---------------------------------------------------------------------------


def test_pipeline_raises_partial_extraction_error_with_data_preserved(
    monkeypatch, tmp_path
):
    pages = {1: page_payload(1), 2: page_payload(2), 3: page_payload(3)}
    failures = {3: FakeResponse(status_code=500)}
    handler = make_page_handler(pages, failures)

    monkeypatch.setattr(
        "extractors.api_extractor.requests.Session",
        lambda: FakeSession(handler),
    )

    config = {
        "pipeline": {
            "name": "api_pipeline",
            "source": {
                "type": "api",
                "url": "https://api.example.com/things",
                "records_path": "data.items",
                "pagination": {"page_param": "page", "max_pages": 10},
                "retry": {"max_retries": 1},
            },
            "target": {"type": "lake", "path": str(tmp_path / "out.csv")},
        }
    }

    pipeline = ETLPipeline(config, root=tmp_path)
    with pytest.raises(PartialExtractionError) as exc_info:
        pipeline.run()

    error = exc_info.value
    assert error.failed_pages[0]["page"] == 3
    assert len(error.frame) == 4
    lineage_stages = [event.stage for event in pipeline.lineage.events]
    assert "extract:api" in lineage_stages
    event = next(
        event for event in pipeline.lineage.events if event.stage == "extract:api"
    )
    assert event.details["status"] == "partial"
    assert event.details["pages_fetched"] == [1, 2]
    assert event.details["failed_pages"][0]["page"] == 3
    # Nothing was loaded.
    assert not (tmp_path / "out.csv").exists()


# ---------------------------------------------------------------------------
# Real local HTTP server acceptance tests
# ---------------------------------------------------------------------------


class _PagingHandler(BaseHTTPRequestHandler):
    """Serves page-param based JSON; controlled via class-level knobs."""

    page_size: ClassVar[int] = 3
    total_records: ClassVar[int] = 7
    fail_pages: ClassVar[dict] = {}
    request_log: ClassVar[list] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        page = int(query.get("page", ["1"])[0])
        type(self).request_log.append(page)

        if page in type(self).fail_pages:
            status = type(self).fail_pages[page]
            self.send_response(status)
            self.end_headers()
            return

        start = (page - 1) * type(self).page_size
        end = min(start + type(self).page_size, type(self).total_records)
        items = [{"id": i, "page": page} for i in range(start, end)]
        body = json.dumps({"data": {"items": items}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def paging_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PagingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _PagingHandler.request_log = []
    _PagingHandler.fail_pages = {}
    _PagingHandler.retry_after_fail = set()
    yield f"http://127.0.0.1:{server.server_address[1]}/orders", server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_real_server_normal_pagination_merges_all_pages(paging_server):
    url, _ = paging_server
    result = APIExtractor(
        url,
        records_path="data.items",
        pagination={
            "page_param": "page",
            "start_page": 1,
            "page_size": 3,
            "max_pages": 20,
        },
    ).extract()

    assert result.ok
    assert result.stop_reason == "empty_page"
    assert result.pages_fetched == [1, 2, 3]
    assert list(result.frame["id"]) == list(range(7))
    # Pages 1,2,3 have data; page 4 is the empty terminator; nothing after it.
    assert _PagingHandler.request_log == [1, 2, 3, 4]


def test_real_server_persistent_failure_preserves_partial_data(paging_server):
    url, _ = paging_server
    _PagingHandler.fail_pages = {3: 500}

    result = APIExtractor(
        url,
        records_path="data.items",
        pagination={"page_param": "page", "max_pages": 20},
        retry={"max_retries": 2, "backoff_seconds": 0},
    ).extract()

    assert not result.ok
    assert result.status == "partial"
    assert result.pages_fetched == [1, 2]
    assert len(result.frame) == 6
    assert result.failed_pages[0]["page"] == 3
    assert _PagingHandler.request_log == [1, 2, 3, 3, 3]


def test_real_server_retry_recovers_from_rate_limiting(paging_server):
    url, _ = paging_server

    call_count = {"page2": 0}
    original_do_get = _PagingHandler.do_GET

    def do_GET_with_recovery(self):
        query = parse_qs(urlparse(self.path).query)
        page = int(query.get("page", ["1"])[0])
        if page == 2:
            call_count["page2"] += 1
            if call_count["page2"] == 1:
                # First attempt is rate-limited; the retry succeeds.
                _PagingHandler.request_log.append(page)
                self.send_response(429)
                self.end_headers()
                return
        original_do_get(self)

    _PagingHandler.do_GET = do_GET_with_recovery
    try:
        result = APIExtractor(
            url,
            records_path="data.items",
            pagination={"page_param": "page", "max_pages": 20},
            retry={"max_retries": 2, "backoff_seconds": 0},
        ).extract()
    finally:
        _PagingHandler.do_GET = original_do_get

    assert result.ok
    assert result.pages_fetched == [1, 2, 3]
    assert len(result.frame) == 7
    assert _PagingHandler.request_log == [1, 2, 2, 3, 4]
