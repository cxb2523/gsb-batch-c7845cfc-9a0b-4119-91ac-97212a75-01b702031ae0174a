import httpx
import pytest

from diffbot import APIError, AuthError, CrawlMonitorError, Diffbot, ValidationError
from diffbot import CrawlEvent, CrawlEventType


def make_client(handler) -> Diffbot:
    return Diffbot(token="test-token", transport=httpx.MockTransport(handler))


CSV_HEADER = "Url,Crawled Time,Crawl Status"


def csv_for(url: str, status: str = "Success") -> str:
    return f"{CSV_HEADER}\n{url},2026-01-01T00:00:00Z,{status}"


def job_running() -> dict:
    return {"jobs": [{"name": "test-job", "jobStatus": {"status": 0, "message": ""}}]}


def job_done() -> dict:
    return {"jobs": [{"name": "test-job", "jobStatus": {"status": 1, "message": "Completed"}}]}


def job_failed() -> dict:
    return {"jobs": [{"name": "test-job", "jobStatus": {"status": 2, "message": "Crawl failed"}}]}


def is_create(request: httpx.Request) -> bool:
    return "seeds" in request.url.params


def is_status(request: httpx.Request) -> bool:
    return not is_create(request) and request.url.path.endswith("/crawl")


def is_urls(request: httpx.Request) -> bool:
    return request.url.path.endswith("/crawl/data")


def test_crawl_returns_job_created_event():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": "Successfully added urls for spidering.", "jobs": [{"name": "test-job"}]})

    db = make_client(handler)
    event = next(db.crawl("https://example.com", job_name="test-job"))
    assert event.event_type == CrawlEventType.JOB_CREATED
    assert event.details["job_name"] == "test-job"


def test_crawl_sends_token_and_seeds():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"jobs": [{"name": "test-job"}]})

    db = make_client(handler)
    next(db.crawl("https://example.com", job_name="test-job"))
    assert captured["params"]["token"] == "test-token"
    assert captured["params"]["seeds"] == "https://example.com"


def test_crawl_normalizes_url():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["seeds"] = request.url.params["seeds"]
        return httpx.Response(200, json={"jobs": [{"name": "test-job"}]})

    db = make_client(handler)
    next(db.crawl("example.com", job_name="test-job"))
    assert captured["seeds"] == "https://example.com"


def test_crawl_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={
            "code": 401,
            "message": "Unauthorized",
            "requestId": "123456789",
            "error": "Not authorized API token.",
            "errorCode": 401,
        })

    db = make_client(handler)
    with pytest.raises(AuthError) as exc_info:
        next(db.crawl("https://example.com"))
    err = exc_info.value
    assert err.status_code == 401
    assert err.message == "Unauthorized"
    assert err.request_id == "123456789"


# ---------------------------------------------------------------------------
# watch=True: polling, retries and interruption
# ---------------------------------------------------------------------------


def drain(generator):
    """Collect events from a generator, returning (events, error)."""
    events = []
    error = None
    try:
        for event in generator:
            events.append(event)
    except BaseException as exc:  # keep the exception for assertions
        error = exc
    return events, error


def test_crawl_watch_completes_normally():
    calls = {"status": 0, "urls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if is_create(request):
            return httpx.Response(200, json={"jobs": [{"name": "test-job"}]})
        if is_status(request):
            calls["status"] += 1
            # Running once, then finished.
            return httpx.Response(200, json=job_running() if calls["status"] == 1 else job_done())
        if is_urls(request):
            calls["urls"] += 1
            # Same URL returned twice by the API: dedup must keep one event.
            return httpx.Response(
                200,
                text=csv_for("https://example.com/a") + "\n"
                     + "https://example.com/a,2026-01-01T00:01:00Z,Success",
            )
        raise AssertionError(f"unexpected request: {request.url}")

    db = make_client(handler)
    events, error = drain(db.crawl(
        "https://example.com",
        job_name="test-job",
        watch=True,
        poll_interval=0,
        retry_interval=0,
    ))

    assert error is None
    assert calls == {"status": 2, "urls": 2}
    assert events[0].event_type == CrawlEventType.JOB_CREATED
    url_events = [e for e in events if e.event_type == CrawlEventType.URL_PROCESSED]
    assert len(url_events) == 1  # dedup preserved
    assert url_events[0].details["url"] == "https://example.com/a"
    assert all(e.event_type != CrawlEventType.MONITOR_INTERRUPTED for e in events)


def test_crawl_watch_retries_after_single_failure_then_succeeds():
    calls = {"status": 0, "urls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if is_create(request):
            return httpx.Response(200, json={"jobs": [{"name": "test-job"}]})
        if is_status(request):
            calls["status"] += 1
            if calls["status"] == 1:
                # First poll's status query fails; the retry succeeds.
                raise httpx.ConnectError("temporary network outage")
            return httpx.Response(200, json=job_done())
        if is_urls(request):
            calls["urls"] += 1
            return httpx.Response(200, text=csv_for("https://example.com/b"))
        raise AssertionError(f"unexpected request: {request.url}")

    db = make_client(handler)
    events, error = drain(db.crawl(
        "https://example.com",
        job_name="test-job",
        watch=True,
        poll_interval=0,
        max_retries=2,
        retry_interval=0,
    ))

    assert error is None
    # Failed initial attempt + successful retry (which already sees completion).
    assert calls["status"] == 2
    assert [e.event_type for e in events] == [
        CrawlEventType.JOB_CREATED,
        CrawlEventType.URL_PROCESSED,
    ]
    assert events[1].details["url"] == "https://example.com/b"


def test_crawl_watch_retries_exhausted_interrupts_and_keeps_events():
    calls = {"status": 0, "urls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if is_create(request):
            return httpx.Response(200, json={"jobs": [{"name": "test-job"}]})
        if is_status(request):
            calls["status"] += 1
            if calls["status"] == 1:
                # First poll succeeds and yields a URL event ...
                return httpx.Response(200, json=job_running())
            # ... every later status query fails until retries are exhausted.
            raise httpx.ConnectError("network down")
        if is_urls(request):
            calls["urls"] += 1
            if calls["urls"] == 1:
                return httpx.Response(200, text=csv_for("https://example.com/c"))
            return httpx.Response(503, text="service unavailable")
        raise AssertionError(f"unexpected request: {request.url}")

    db = make_client(handler)
    max_retries = 2
    events, error = drain(db.crawl(
        "https://example.com",
        job_name="test-job",
        watch=True,
        poll_interval=0,
        max_retries=max_retries,
        retry_interval=0,
    ))

    assert isinstance(error, CrawlMonitorError)
    assert error.job_name == "test-job"
    assert error.attempts == max_retries + 1
    assert isinstance(error.last_error, httpx.ConnectError)
    assert isinstance(error.__cause__, httpx.ConnectError)

    # The explicit termination signal is the final event ...
    assert events[-1].event_type == CrawlEventType.MONITOR_INTERRUPTED
    assert events[-1].details["job_name"] == "test-job"
    # ... and everything yielded before the interruption is retained.
    assert events[0].event_type == CrawlEventType.JOB_CREATED
    assert events[1].event_type == CrawlEventType.URL_PROCESSED
    assert events[1].details["url"] == "https://example.com/c"

    # Initial poll + (initial attempt + max_retries retries) on the failing poll.
    assert calls["status"] == 1 + (max_retries + 1)


def test_crawl_watch_job_reported_failure_raises_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if is_create(request):
            return httpx.Response(200, json={"jobs": [{"name": "test-job"}]})
        if is_status(request):
            return httpx.Response(200, json=job_failed())
        if is_urls(request):
            return httpx.Response(200, content=b"")
        raise AssertionError(f"unexpected request: {request.url}")

    db = make_client(handler)
    events, error = drain(db.crawl(
        "https://example.com",
        job_name="test-job",
        watch=True,
        poll_interval=0,
        retry_interval=0,
    ))

    assert isinstance(error, APIError)
    assert "Crawler job failed" in str(error)
    assert all(e.event_type != CrawlEventType.MONITOR_INTERRUPTED for e in events)


def test_crawl_watch_invalid_options_raise_validation_error():
    db = make_client(lambda request: httpx.Response(200, json={}))
    with pytest.raises(ValidationError):
        list(db.crawl("https://example.com", watch=True, max_retries=-1))
    with pytest.raises(ValidationError):
        list(db.crawl("https://example.com", watch=True, retry_interval=-1))