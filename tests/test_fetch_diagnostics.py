"""Fetch diagnostics: redirect credential handling and Retry-After pacing.

These tests drive the policy with the in-repo fake session (no external
network) and, where real socket behaviour is useful, a small loopback HTTP
server. A deterministic clock/sleep scheduler makes the waits observable
without slowing the suite.
"""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from http_fakes import FakeResponse, FakeSession

from newsworker import fetch as fetch_module
from newsworker.fetch import (
    FetchPolicy,
    FetchRequest,
    RateLimited,
    RequestsTransport,
    strip_cross_origin_headers,
)


class DeterministicScheduler(fetch_module.HostScheduler):
    """Replaces the process scheduler: records sleeps instead of blocking."""

    def __init__(self):
        super().__init__(clock=lambda: self.now, sleep=self._sleep)
        self.now = 1000.0
        self.sleeps = []

    def _sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


@pytest.fixture
def fake_clock(monkeypatch):
    scheduler = DeterministicScheduler()
    monkeypatch.setattr(fetch_module, "HOSTS", scheduler)
    return scheduler


def _policy(**values):
    return FetchPolicy(block_private_hosts=False, **values)


SENSITIVE_POLICY = FetchPolicy(
    block_private_hosts=False,
    user_agent="agent/1",
    extra_headers={
        "Authorization": "Bearer secret",
        "Cookie": "sid=1",
        "X-Api-Key": "k-123",
        "X-Custom": "mine",
    },
)


def test_same_origin_redirect_keeps_every_header():
    session = FakeSession(
        [
            FakeResponse(301, {"Location": "/final"}),
            FakeResponse(200, {}, [b"ok"]),
        ]
    )
    result = RequestsTransport(session).fetch(
        FetchRequest("https://example.com/start"), SENSITIVE_POLICY
    )
    first, second = (call[2]["headers"] for call in session.calls)
    for name in ("Authorization", "Cookie", "X-Api-Key", "X-Custom"):
        assert first[name] == second[name]
    assert second["User-agent"] == "agent/1"
    assert result.status == 200
    assert result.redirect_chain == ("https://example.com/final",)
    (hop,) = result.redirects
    assert hop.cross_origin is False and hop.dropped_headers == ()
    assert hop.status == 301 and hop.url.endswith("/final")
    assert result.rate_limit_waits == () and result.rate_limit_wait_seconds == 0


def test_cross_origin_redirect_strips_sensitive_and_configured_headers():
    session = FakeSession(
        [
            FakeResponse(302, {"Location": "https://other.example/landing"}),
            FakeResponse(200, {}, [b"ok"]),
        ]
    )
    result = RequestsTransport(session).fetch(
        FetchRequest("https://example.com/start"), SENSITIVE_POLICY
    )
    first = session.calls[0][2]["headers"]
    second = session.calls[1][2]["headers"]
    assert first["Authorization"] == "Bearer secret"
    for name in ("Authorization", "Cookie", "X-Api-Key", "X-Custom"):
        assert name not in second
    # Only the user agent follows the redirect to the new origin.
    assert second == {"User-agent": "agent/1"}
    (hop,) = result.redirects
    assert hop.cross_origin is True
    assert set(hop.dropped_headers) == {"Authorization", "Cookie", "X-Api-Key", "X-Custom"}
    assert result.redirect_chain == ("https://other.example/landing",)


def test_cross_origin_strip_helper_reports_dropped_names():
    headers = {
        "User-agent": "agent/1",
        "Authorization": "Bearer x",
        "X-Api-Key": "k",
        "Accept": "text/html",
    }
    kept, dropped = strip_cross_origin_headers(
        headers, FetchPolicy(extra_headers={"X-Api-Key": "k"}), keep=("User-agent",)
    )
    assert kept == {"User-agent": "agent/1", "Accept": "text/html"}
    assert set(dropped) == {"Authorization", "X-Api-Key"}


def test_429_retry_after_is_waited_then_retried_and_diagnosed(fake_clock):
    session = FakeSession(
        [
            FakeResponse(429, {"Retry-After": "3"}),
            FakeResponse(200, {}, [b"ok"]),
        ]
    )
    result = RequestsTransport(session).fetch(
        FetchRequest("https://example.com/page"), _policy(max_host_wait=30)
    )
    assert len(session.calls) == 2 and session.calls[0][1] == session.calls[1][1]
    assert fake_clock.sleeps == [3.0]
    (wait,) = result.rate_limit_waits
    assert wait.host == "example.com"
    assert wait.requested == 3.0 and wait.waited == 3.0
    assert result.rate_limit_wait_seconds == 3.0
    # The not-before window is recorded for the host so later requests obey it.
    state = fake_clock._hosts["example.com"]
    assert state.not_before == pytest.approx(1000.0 + 3.0)
    assert state.not_before >= state.next_start


def test_503_retry_after_spaces_subsequent_requests(fake_clock):
    session = FakeSession(
        [
            FakeResponse(503, {"Retry-After": "4"}),
            FakeResponse(200, {}, [b"ok"]),
            FakeResponse(200, {}, [b"again"]),
        ]
    )
    transport = RequestsTransport(session)
    first = transport.fetch(
        FetchRequest("https://example.com/a"), _policy(host_interval=0, max_host_wait=30)
    )
    assert first.rate_limit_waits[0].waited == 4.0
    # The retry request's own slot consumed the wait, but the scheduler still
    # carries the not-before window inherited by every later request.
    assert fake_clock._hosts["example.com"].not_before == pytest.approx(1000.0 + 4.0)
    second = transport.fetch(
        FetchRequest("https://example.com/b"), _policy(host_interval=0, max_host_wait=30)
    )
    assert second.rate_limit_waits == ()
    # Only the Retry-After window is recorded; ordinary pacing sleeps are the
    # scheduler's slot business and carry no rate-limit diagnostic.
    assert fake_clock.sleeps == [4.0]


def test_retry_after_over_max_host_wait_raises_named_rate_limit_error(fake_clock):
    session = FakeSession([FakeResponse(429, {"Retry-After": "120"})])
    with pytest.raises(RateLimited, match=r"example\.com.*120.*5"):
        RequestsTransport(session).fetch(
            FetchRequest("https://example.com/page"), _policy(max_host_wait=5)
        )
    assert fake_clock.sleeps == []  # fails fast, never parks the worker
    assert len(session.calls) == 1


def test_repeated_rate_limiting_ends_after_the_retry_budget(fake_clock):
    session = FakeSession(
        [FakeResponse(429, {"Retry-After": "1"}) for _ in range(4)]
    )
    with pytest.raises(RateLimited, match="kept returning 429"):
        RequestsTransport(session).fetch(
            FetchRequest("https://example.com/page"),
            _policy(max_host_wait=30, rate_limit_retries=3),
        )
    assert len(session.calls) == 4  # initial attempt + three retries
    assert fake_clock.sleeps == [1.0, 1.0, 1.0]


def test_cross_origin_redirect_then_rate_limit_combines_diagnostics(fake_clock):
    session = FakeSession(
        [
            FakeResponse(302, {"Location": "https://other.example/landing"}),
            FakeResponse(503, {"Retry-After": "2"}),
            FakeResponse(200, {}, [b"ok"]),
        ]
    )
    result = RequestsTransport(session).fetch(
        FetchRequest("https://example.com/start"),
        SENSITIVE_POLICY,
    )
    # Credentials were stripped before the first request to the new origin...
    second_call_headers = session.calls[1][2]["headers"]
    assert "Authorization" not in second_call_headers
    # ...and the Retry-After window on that origin was still honoured.
    assert fake_clock.sleeps == [2.0]
    (hop,) = result.redirects
    assert hop.cross_origin is True and hop.dropped_headers
    (wait,) = result.rate_limit_waits
    assert wait.host == "other.example" and wait.waited == 2.0
    assert result.effective_url == "https://other.example/landing"


def test_429_without_retry_after_still_raises_http_error(fake_clock):
    import requests

    session = FakeSession([FakeResponse(429, {})])
    with pytest.raises(requests.HTTPError):
        RequestsTransport(session).fetch(
            FetchRequest("https://example.com/page"), _policy()
        )
    assert fake_clock.sleeps == []


def test_delivery_never_sleeps_on_retry_after_but_marks_the_host(fake_clock):
    session = FakeSession([FakeResponse(503, {"Retry-After": "60"}, [b"busy"])])
    result = RequestsTransport(session).fetch(
        FetchRequest("https://example.com/hook", operation="delivery"),
        _policy(max_host_wait=5),
    )
    assert result.status == 503 and result.body == b"busy"
    assert fake_clock.sleeps == []
    # Source fetches to that host still inherit the pacing window.
    with pytest.raises(RateLimited):
        fake_clock.reserve("example.com", max_wait=5)


class _RedirectHandler(BaseHTTPRequestHandler):
    seen: list = []

    def do_GET(self):  # noqa: N802
        _RedirectHandler.seen.append((self.path, dict(self.headers)))
        port = self.server.server_address[1]
        if self.path == "/same":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/page")
            self.end_headers()
            return
        if self.path == "/cross":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/page")
            self.end_headers()
            return
        body = b"<html>ok</html>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def redirect_server():
    _RedirectHandler.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_real_server_same_origin_keeps_credentials(redirect_server):
    policy = FetchPolicy(
        block_private_hosts=False,
        extra_headers={"Authorization": "Bearer real", "X-Site-Token": "t"},
    )
    result = RequestsTransport().fetch(
        FetchRequest(f"http://127.0.0.1:{redirect_server}/same"), policy
    )
    assert result.effective_url.endswith("/page")
    first, second = _RedirectHandler.seen
    assert first[1]["Authorization"] == "Bearer real"
    assert second[1]["Authorization"] == "Bearer real"
    assert result.redirects[0].cross_origin is False


def test_real_server_cross_origin_drops_credentials(redirect_server):
    policy = FetchPolicy(
        block_private_hosts=False,
        user_agent="real-agent",
        extra_headers={"Authorization": "Bearer real", "X-Site-Token": "t"},
    )
    result = RequestsTransport().fetch(
        FetchRequest(f"http://127.0.0.1:{redirect_server}/cross"), policy
    )
    first, second = _RedirectHandler.seen
    assert first[1]["Authorization"] == "Bearer real"
    assert "Authorization" not in second[1] and "X-Site-Token" not in second[1]
    agent = next(value for key, value in second[1].items() if key.lower() == "user-agent")
    assert agent == "real-agent"
    assert result.redirects[0].cross_origin is True
    assert set(result.redirects[0].dropped_headers) == {"Authorization", "X-Site-Token"}


class _RetryAfterHandler(BaseHTTPRequestHandler):
    attempts = 0
    seen: list = []

    def do_GET(self):  # noqa: N802
        _RetryAfterHandler.seen.append(time.monotonic())
        _RetryAfterHandler.attempts += 1
        if _RetryAfterHandler.attempts == 1:
            self.send_response(429)
            self.send_header("Retry-After", "1")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"recovered"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def test_real_server_waits_retry_after_before_retrying():
    _RetryAfterHandler.attempts = 0
    _RetryAfterHandler.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _RetryAfterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        started = time.monotonic()
        result = RequestsTransport().fetch(
            FetchRequest(f"http://127.0.0.1:{port}/feed"),
            FetchPolicy(block_private_hosts=False, max_host_wait=10),
        )
        elapsed = time.monotonic() - started
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert result.status == 200 and result.body == b"recovered"
    assert _RetryAfterHandler.attempts == 2
    assert _RetryAfterHandler.seen[1] - _RetryAfterHandler.seen[0] >= 1.0
    assert elapsed >= 1.0
    assert result.rate_limit_waits[0].waited == pytest.approx(1.0, abs=0.05)
