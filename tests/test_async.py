import httpx
import pytest

from diffbot import APIError, AuthError, CrawlMonitorError, DiffbotAsync
from diffbot import CrawlEventType


def make_async_client(handler) -> DiffbotAsync:
    return DiffbotAsync(token="test-token", transport=httpx.MockTransport(handler))


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


async def adrain(generator):
    """Collect events from an async generator, returning (events, error)."""
    events = []
    error = None
    try:
        async for event in generator:
            events.append(event)
    except BaseException as exc:
        error = exc
    return events, error


@pytest.mark.anyio
async def test_async_extract_returns_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["token"] == "test-token"
        assert request.url.params["url"] == "https://example.com"
        return httpx.Response(200, json={"objects": [{"title": "Example"}]})

    db = make_async_client(handler)
    result = await db.extract("https://example.com")
    assert result == {"objects": [{"title": "Example"}]}


@pytest.mark.anyio
async def test_async_crawl_returns_job_created():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": [{"name": "test-job"}]})

    db = make_async_client(handler)
    events = [event async for event in db.crawl("https://example.com", job_name="test-job")]
    assert len(events) == 1
    assert events[0].event_type == CrawlEventType.JOB_CREATED
    assert events[0].details["job_name"] == "test-job"


@pytest.mark.anyio
async def test_async_context_manager_closes_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with DiffbotAsync(token="t", transport=httpx.MockTransport(handler)) as db:
        await db.extract("example.com")
    assert db._http.is_closed


@pytest.mark.anyio
async def test_async_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={
            "code": 401,
            "message": "Unauthorized",
            "requestId": "123456789",
            "error": "Not authorized API token.",
            "errorCode": 401,
        })

    db = make_async_client(handler)
    with pytest.raises(AuthError) as exc_info:
        await db.extract("https://example.com")
    err = exc_info.value
    assert err.status_code == 401
    assert err.message == "Unauthorized"
    assert err.request_id == "123456789"


# ---------------------------------------------------------------------------
# watch=True: polling, retries and interruption
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_async_crawl_watch_completes_normally():
    calls = {"status": 0, "urls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if is_create(request):
            return httpx.Response(200, json={"jobs": [{"name": "test-job"}]})
        if is_status(request):
            calls["status"] += 1
            return httpx.Response(200, json=job_running() if calls["status"] == 1 else job_done())
        if is_urls(request):
            calls["urls"] += 1
            return httpx.Response(
                200,
                text=csv_for("https://example.com/a") + "\n"
                     + "https://example.com/a,2026-01-01T00:01:00Z,Success",
            )
        raise AssertionError(f"unexpected request: {request.url}")

    db = make_async_client(handler)
    events, error = await adrain(db.crawl(
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


@pytest.mark.anyio
async def test_async_crawl_watch_retries_after_single_failure_then_succeeds():
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

    db = make_async_client(handler)
    events, error = await adrain(db.crawl(
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


@pytest.mark.anyio
async def test_async_crawl_watch_retries_exhausted_interrupts_and_keeps_events():
    calls = {"status": 0, "urls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if is_create(request):
            return httpx.Response(200, json={"jobs": [{"name": "test-job"}]})
        if is_status(request):
            calls["status"] += 1
            if calls["status"] == 1:
                return httpx.Response(200, json=job_running())
            raise httpx.ConnectError("network down")
        if is_urls(request):
            calls["urls"] += 1
            if calls["urls"] == 1:
                return httpx.Response(200, text=csv_for("https://example.com/c"))
            return httpx.Response(503, text="service unavailable")
        raise AssertionError(f"unexpected request: {request.url}")

    db = make_async_client(handler)
    max_retries = 2
    events, error = await adrain(db.crawl(
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

    assert calls["status"] == 1 + (max_retries + 1)


@pytest.mark.anyio
async def test_async_crawl_watch_job_reported_failure_raises_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if is_create(request):
            return httpx.Response(200, json={"jobs": [{"name": "test-job"}]})
        if is_status(request):
            return httpx.Response(200, json=job_failed())
        if is_urls(request):
            return httpx.Response(200, content=b"")
        raise AssertionError(f"unexpected request: {request.url}")

    db = make_async_client(handler)
    events, error = await adrain(db.crawl(
        "https://example.com",
        job_name="test-job",
        watch=True,
        poll_interval=0,
        retry_interval=0,
    ))

    assert isinstance(error, APIError)
    assert "Crawler job failed" in str(error)
    assert all(e.event_type != CrawlEventType.MONITOR_INTERRUPTED for e in events)


"""
Live
"""

@pytest.mark.live
@pytest.mark.anyio
async def test_live_async_extract(live_token):
    async with DiffbotAsync(token=live_token) as db:
        result = await db.extract("https://example.com")
    assert "objects" in result
    assert len(result["objects"]) > 0
    assert "content" in result["objects"][0]


@pytest.mark.live
@pytest.mark.anyio
async def test_live_async_dql(live_token):
    async with DiffbotAsync(token=live_token) as db:
        result = await db.dql('type:Organization name:"Diffbot"', size=1)
    assert "data" in result
    assert len(result["data"]) > 0


@pytest.mark.live
@pytest.mark.anyio
async def test_live_async_web_search(live_token):
    async with DiffbotAsync(token=live_token) as db:
        result = await db.web_search("diffbot knowledge graph", num_results=3)
    assert "search_results" in result
