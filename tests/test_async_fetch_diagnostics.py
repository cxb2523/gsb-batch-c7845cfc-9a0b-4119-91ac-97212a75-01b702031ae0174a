"""Async transport diagnostics mirror the synchronous policy.

Everything runs against a loopback HTTP server; waits use Retry-After values
short enough to keep the suite fast.
"""

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("aiohttp")

from newsworker import async_fetch  # noqa: E402
from newsworker.async_fetch import fetch_urls_async  # noqa: E402
from newsworker.fetch import FetchPolicy, RateLimited  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    seen: list = []
    rate_attempts = 0

    def do_GET(self):  # noqa: N802
        Handler.seen.append((self.path, dict(self.headers)))
        port = self.server.server_address[1]
        if self.path == "/same":
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{port}/page")
            self.end_headers()
            return
        if self.path == "/cross":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/page")
            self.end_headers()
            return
        if self.path == "/limited":
            Handler.rate_attempts += 1
            if Handler.rate_attempts == 1:
                self.send_response(429)
                self.send_header("Retry-After", "1")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        body = b"async-ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def server():
    Handler.seen = []
    Handler.rate_attempts = 0
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _policy(port, **values):
    values.setdefault("max_host_wait", 10)
    return FetchPolicy(block_private_hosts=False, **values)


def test_async_same_origin_redirect_keeps_credentials(server):
    url = f"http://127.0.0.1:{server}/same"
    policy = _policy(server, extra_headers={"Authorization": "Bearer a", "X-Api-Key": "k"})
    result = asyncio.run(fetch_urls_async([url], policy=policy))[url]
    assert result.status == 200 and result.effective_url.endswith("/page")
    first, second = Handler.seen
    assert first[1]["Authorization"] == "Bearer a"
    assert second[1]["Authorization"] == "Bearer a" and second[1]["X-Api-Key"] == "k"
    (hop,) = result.redirects
    assert hop.status == 307 and hop.cross_origin is False
    assert hop.dropped_headers == ()


def test_async_cross_origin_redirect_strips_credentials(server):
    url = f"http://127.0.0.1:{server}/cross"
    policy = _policy(
        server, user_agent="async-agent", extra_headers={"Authorization": "Bearer a", "X-Api-Key": "k"}
    )
    result = asyncio.run(fetch_urls_async([url], policy=policy))[url]
    first, second = Handler.seen
    assert first[1]["Authorization"] == "Bearer a"
    assert "Authorization" not in second[1] and "X-Api-Key" not in second[1]
    agent = next(v for k, v in second[1].items() if k.lower() == "user-agent")
    assert agent == "async-agent"
    (hop,) = result.redirects
    assert hop.cross_origin is True
    assert set(hop.dropped_headers) == {"Authorization", "X-Api-Key"}
    assert result.redirect_chain == (f"http://localhost:{server}/page",)


def test_async_retry_after_wait_is_recorded_and_retried(server):
    url = f"http://127.0.0.1:{server}/limited"
    policy = _policy(server)
    started = time.monotonic()
    result = asyncio.run(fetch_urls_async([url], policy=policy))[url]
    assert time.monotonic() - started >= 1.0
    assert result.status == 200 and Handler.rate_attempts == 2
    (wait,) = result.rate_limit_waits
    assert wait.host == "127.0.0.1" and wait.requested == pytest.approx(1.0)
    assert 0 < wait.waited <= 1.05
    assert result.rate_limit_wait_seconds == wait.waited


def test_async_retry_after_over_limit_fails_with_named_error(server, monkeypatch):
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(async_fetch.asyncio, "sleep", fake_sleep)
    url = f"http://127.0.0.1:{server}/limited"
    policy = _policy(server, max_host_wait=0.01)
    outcome = asyncio.run(fetch_urls_async([url], policy=policy))[url]
    assert isinstance(outcome, RateLimited)
    assert "127.0.0.1" in str(outcome) and "1" in str(outcome)
    assert sleeps == []
