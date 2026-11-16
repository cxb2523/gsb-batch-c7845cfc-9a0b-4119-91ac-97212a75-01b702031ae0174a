"""
End-to-end tests against a locally served fake page.

Instead of a fully stubbed browser, the "browser" here fetches the fake page
over real HTTP from a local server and reports the cookies the server set. This
keeps the flow local and deterministic while still exercising the real HTTP
response, the ``Set-Cookie`` handling and the challenge detection.
"""

from __future__ import annotations

import asyncio
import http.cookies
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

import pytest

import main

CLEARANCE_VALUE = "fake-page-clearance"

run = asyncio.run


class FakePageHandler(BaseHTTPRequestHandler):
    """
    Serves one of the canned pages.

    ``/plain`` - no challenge, no cookies
    ``/challenge`` - a managed challenge marker, never solved
    ``/clearance`` - sets a cf_clearance cookie
    """

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/challenge"):
            body = b"<html><script>cType: 'managed'</script></html>"
            cookie = None
        elif self.path.startswith("/clearance"):
            body = b"<html><body>welcome</body></html>"
            cookie = f"cf_clearance={CLEARANCE_VALUE}; Path=/; Max-Age=3600"
        else:
            body = b"<html><body>nothing to see</body></html>"
            cookie = None

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        return None


@pytest.fixture()
def fake_page_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakePageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class HttpStubSolver:
    """
    A solver whose "browser" is a plain HTTP client.

    ``solved_after_seconds`` makes the challenge page hand out a cookie after
    that delay (``None`` means it never solves).
    """

    def __init__(
        self,
        *,
        timeout: float,
        solved_after_seconds: Optional[float] = None,
        **_: Any,
    ) -> None:
        self._timeout = timeout
        self._solved_after = solved_after_seconds
        self._url = ""
        self._cookies: List[Dict[str, Any]] = []
        self._html = ""
        self.started = False
        self.stopped = False
        self.proxy_failure_reason: Optional[str] = None
        self.click_times: List[float] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def request_page(self, url: str) -> None:
        self._url = url
        request = urllib.request.Request(url)

        with urllib.request.urlopen(request, timeout=5) as response:
            self._html = response.read().decode()
            headers = response.headers.get_all("Set-Cookie") or []

        self._cookies = []
        for header in headers:
            parsed = http.cookies.SimpleCookie(header)
            for name, morsel in parsed.items():
                self._cookies.append(
                    {
                        "name": name,
                        "value": morsel.value,
                        "domain": "127.0.0.1",
                        "path": morsel["path"] or "/",
                    }
                )

    async def get_cookies(self) -> List[Dict[str, Any]]:
        return [dict(cookie) for cookie in self._cookies]

    async def get_user_agent(self) -> str:
        return "fake-page-browser/1.0"

    async def set_user_agent_metadata(self, user_agent: str) -> None:
        return None

    async def detect_challenge(self) -> Optional[main.ChallengePlatform]:
        for platform in main.ChallengePlatform:
            if f"cType: '{platform.value}'" in self._html:
                return platform
        return None

    async def solve_challenge(self, timeout: Optional[float] = None) -> bool:
        budget = self._timeout if timeout is None else timeout

        if self._solved_after is not None and self._solved_after <= budget:
            self.click_times.append(asyncio.get_running_loop().time())
            await asyncio.sleep(self._solved_after)
            self._cookies.append(
                {
                    "name": "cf_clearance",
                    "value": CLEARANCE_VALUE,
                    "domain": "127.0.0.1",
                    "path": "/",
                }
            )
            return True

        # Never solves: keep "clicking" until the budget runs out.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        while loop.time() < deadline:
            self.click_times.append(loop.time())
            await asyncio.sleep(min(0.05, max(deadline - loop.time(), 0.0)))

        raise main.ChallengeTimeoutError("challenge timed out")


def solver_factory(**defaults: Any):
    def factory(**kwargs: Any) -> HttpStubSolver:
        return HttpStubSolver(**{**defaults, **kwargs})

    return factory


def test_fake_page_with_clearance_cookie(fake_page_server) -> None:
    result = run(
        main.collect_clearance(
            f"{fake_page_server}/clearance",
            timeout=5,
            solver_factory=solver_factory(),
        )
    )

    assert result.status is main.CollectionStatus.SUCCESS
    assert result.cf_clearance is not None
    assert result.cf_clearance["value"] == CLEARANCE_VALUE
    assert result.user_agent == "fake-page-browser/1.0"
    assert result.error is None
    assert 0 <= result.elapsed < 10


def test_fake_page_without_challenge(fake_page_server) -> None:
    result = run(
        main.collect_clearance(
            f"{fake_page_server}/plain",
            timeout=5,
            solver_factory=solver_factory(),
        )
    )

    assert result.status is main.CollectionStatus.NO_CHALLENGE
    assert result.ok
    assert result.challenge_type is None
    assert result.cf_clearance is None
    assert result.cookies == []


def test_fake_page_challenge_timeout(fake_page_server) -> None:
    import time

    started = time.monotonic()
    result = run(
        main.collect_clearance(
            f"{fake_page_server}/challenge",
            timeout=0.6,
            solver_factory=solver_factory(),
        )
    )
    duration = time.monotonic() - started

    assert result.status is main.CollectionStatus.TIMEOUT
    assert result.challenge_type is main.ChallengePlatform.MANAGED
    assert result.error is not None
    assert "0.6" in result.error
    assert duration < 0.6 + 2


def test_fake_page_challenge_solved_after_a_click(fake_page_server) -> None:
    result = run(
        main.collect_clearance(
            f"{fake_page_server}/challenge",
            timeout=5,
            solver_factory=solver_factory(solved_after_seconds=0.1),
        )
    )

    assert result.status is main.CollectionStatus.SUCCESS
    assert result.cf_clearance is not None
    assert result.challenge_type is main.ChallengePlatform.MANAGED


def test_fake_page_cli_and_interface_report_the_same_status(
    fake_page_server, capsys, monkeypatch
) -> None:
    import json

    factory = solver_factory(solved_after_seconds=0.1)

    # 1. the programmatic interface
    result = run(
        main.collect_clearance(
            f"{fake_page_server}/challenge", timeout=5, solver_factory=factory
        )
    )

    # 2. the command line entry point, same input
    monkeypatch.setattr(main, "CloudflareSolver", factory)
    exit_code = run(
        main.main(["--json", "-t", "5", f"{fake_page_server}/challenge"])
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == result.status.value
    assert payload["challenge_type"] == result.challenge_type.value
    assert bool(payload["cf_clearance"]) is bool(result.cf_clearance)
    assert payload["user_agent"] == result.user_agent


def test_fake_page_cli_timeout_status_matches_interface(
    fake_page_server, capsys, monkeypatch
) -> None:
    import json

    factory = solver_factory()

    result = run(
        main.collect_clearance(
            f"{fake_page_server}/challenge", timeout=0.5, solver_factory=factory
        )
    )
    capsys.readouterr()

    monkeypatch.setattr(main, "CloudflareSolver", factory)
    exit_code = run(
        main.main(["--json", "-t", "0.5", f"{fake_page_server}/challenge"])
    )
    payload = json.loads(capsys.readouterr().out)

    assert result.status is main.CollectionStatus.TIMEOUT
    assert exit_code == 1
    assert payload["status"] == result.status.value == "timeout"
    assert payload["error"]
