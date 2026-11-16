"""How a medium that could not be had is accounted for.

A medium missing from an archive replay's result used to leave one vague debug line
behind ("Dropped the medium"), which did not say whether the site throttled it, the
browser's stream broke off, or the file was over the size limit. Now exactly one info
line names the host, the strategies tried in turn (loaded, stashed, frame, network,
http) and how each ended -- and a medium that comes back logs nothing extra.

Exercised with the real browser and a local HTTP server, so the browser's network stack
is really what answers.
"""

import logging

import pytest
from aiohttp import web
from playwright.async_api import async_playwright

from scrapemm.server.download.browser import BrowserMedia
from scrapemm.server.download.util import collecting_omitted

pytestmark = pytest.mark.server

# A pixel-free but structurally sound PNG: the settlement path hands out bytes undecoded
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
       b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
       b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


class Site:
    """A local HTTP server, counting how often each path was asked for."""

    def __init__(self):
        self.hits: dict[str, int] = {}

    def count(self, path: str) -> int:
        self.hits[path] = self.hits.get(path, 0) + 1
        return self.hits[path]


def _is_settlement(message: str) -> bool:
    """Whether a log line is the one line a dropped medium leaves behind."""
    return ("Dropped the medium" in message or "Left out the medium" in message)


@pytest.fixture
async def site():
    state = Site()

    async def forbidden(request):
        state.count(request.path)
        return web.Response(status=403, text="forbidden", content_type="text/plain")

    async def image(request):
        state.count(request.path)
        return web.Response(body=PNG, content_type="image/png")

    async def large(request):
        state.count(request.path)
        return web.Response(body=PNG * 40, content_type="image/png")  # 400+ bytes

    async def throttled(request):
        state.count(request.path)
        return web.Response(status=429, text="slow down", content_type="text/plain")

    app = web.Application()
    app.router.add_get("/forbidden.jpg", forbidden)
    app.router.add_get("/image.png", image)
    app.router.add_get("/large.png", large)
    app.router.add_get("/throttled.jpg", throttled)
    runner = web.AppRunner(app)
    await runner.setup()
    server = web.TCPSite(runner, "127.0.0.1", 0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    state.base = f"http://127.0.0.1:{port}"
    yield state
    await runner.cleanup()


@pytest.fixture
async def page():
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:  # No Chromium for this Playwright
            pytest.skip(f"No Chromium for Playwright: {e}")
        page = await browser.new_page()
        yield page
        await browser.close()


async def test_a_403_medium_is_settled_with_host_strategies_and_status(site, page, caplog):
    """A site that never serves the medium: the browser's copy is absent, the network
    stack sees HTTP 403, then the direct download does -- and the one info line says
    so, instead of leaving the reader to guess between throttling and a broken stream."""
    media = BrowserMedia(page)
    url = f"{site.base}/forbidden.jpg"
    try:
        with caplog.at_level(logging.INFO, logger="scrapeMM"):
            content, _ = await media.get(url)
    finally:
        await media.close()

    assert content is None  # No exception, and nothing to hand out
    lines = [r.getMessage() for r in caplog.records if _is_settlement(r.getMessage())]
    assert len(lines) == 1, lines
    line = lines[0]
    assert site.base.split("//")[1] in line  # The host
    for strategy in ("loaded", "network", "http"):
        assert strategy in line
    assert "403" in line  # The status each fetch ended with
    # The network stack, then the direct download: one request each, no retry on 403
    assert site.hits["/forbidden.jpg"] == 2


async def test_a_throttled_medium_keeps_its_backoff_and_says_so(site, page, caplog, monkeypatch):
    """429/503 are still retried with the backoff (untouched); the settlement line then
    says the site kept throttling rather than leaving the reader with a bare status."""
    from scrapemm.server.download import browser as browser_module
    monkeypatch.setattr(browser_module, "THROTTLE_BACKOFF", (0.01, 0.01))
    media = BrowserMedia(page)
    url = f"{site.base}/throttled.jpg"
    try:
        with caplog.at_level(logging.INFO, logger="scrapeMM"):
            content, _ = await media.get(url)
    finally:
        await media.close()

    assert content is None
    # 3 requests per strategy (the first and two retries), through the network stack
    # and then the direct download: the backoff was not skipped
    assert site.hits["/throttled.jpg"] == 6
    lines = [r.getMessage() for r in caplog.records if _is_settlement(r.getMessage())]
    assert len(lines) == 1
    assert "HTTP 429 after 2 retries" in lines[0]


async def test_an_oversized_medium_is_omitted_without_a_retry(site, page, caplog):
    """Content-Length over the limit: reported as left out, and no strategy after the
    one that saw the size is tried -- the size check is not worked around by a retry."""
    media = BrowserMedia(page)
    url = f"{site.base}/large.png"
    try:
        with collecting_omitted() as omitted, caplog.at_level(logging.INFO, logger="scrapeMM"):
            content, _ = await media.get(url, limit=100)
    finally:
        await media.close()

    assert content is None
    assert [(medium.url, medium.limit) for medium in omitted] == [(url, 100)]
    assert site.hits["/large.png"] == 1  # The first strategy saw it; none followed
    # The line names the cause at info level, as a medium left out rather than a failure
    lines = [r.getMessage() for r in caplog.records if _is_settlement(r.getMessage())]
    assert len(lines) == 1 and "Left out" in lines[0] and "too large" in lines[0]


async def test_a_medium_that_replays_is_returned_without_a_failure_line(site, page, caplog):
    """The success path: the bytes come back and no settlement line is written."""
    media = BrowserMedia(page)
    url = f"{site.base}/image.png"
    try:
        with collecting_omitted() as omitted, caplog.at_level(logging.INFO, logger="scrapeMM"):
            content, content_type = await media.get(url)
    finally:
        await media.close()

    assert content == PNG
    assert content_type.startswith("image/png")
    assert omitted == []
    assert not [r for r in caplog.records if _is_settlement(r.getMessage())]


def test_the_settlement_line_names_every_strategy_and_its_outcome():
    """The trace is what makes a dropped medium readable: a throttled site, a stream
    that broke off and a fallback URL are all distinguishable in the one line."""
    from scrapemm.server.download.browser import _Trace
    trace = _Trace("https://cdn.example/a.jpg")
    trace.begin("loaded")
    trace.step("loaded", "the browser has no copy")
    trace.begin("frame")
    trace.step("frame", "Failed to fetch")
    trace.begin("network")
    trace.step("network", "HTTP 429 after 2 retries")
    trace.begin("http")
    trace.step("http", "ClientPayloadError: Response payload is not completed")
    trace.candidate = "https://cdn.example/b.jpg"
    trace.cut_off("no answer within 180 s")

    line = trace.render("cdn.example")
    assert line.startswith("Dropped the medium https://cdn.example/a.jpg from cdn.example:")
    for strategy in ("loaded", "frame", "network", "http", "budget"):
        assert f"{strategy}:" in line or f"{strategy} (" in line
    assert "HTTP 429 after 2 retries" in line
    assert "no answer within 180 s" in line
    assert "https://cdn.example/b.jpg" in line  # The fallback candidate that was tried
