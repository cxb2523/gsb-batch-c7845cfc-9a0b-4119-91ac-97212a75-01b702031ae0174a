"""
Tests for the structured clearance collection interface.

The browser is stubbed so the tests are fully local and deterministic; they
cover the four acceptance paths: success, no challenge, timeout and proxy
authentication failure.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import pytest

import main

CLEARANCE_COOKIE: Dict[str, Any] = {
    "name": "cf_clearance",
    "value": "clearance-value",
    "domain": "example.com",
    "path": "/",
    "size": 15,
    "httpOnly": True,
    "secure": True,
    "session": False,
    "priority": "Medium",
    "sourceScheme": "Secure",
    "sourcePort": 443,
    "expires": 2_000_000_000.0,
}

SESSION_COOKIE: Dict[str, Any] = {
    "name": "session",
    "value": "session-value",
    "domain": "example.com",
    "path": "/",
    "size": 13,
    "httpOnly": False,
    "secure": True,
    "session": True,
    "priority": "Medium",
    "sourceScheme": "Secure",
    "sourcePort": 443,
}

CHALLENGE_HTML = "<html><script>cType: 'managed'</script></html>"
NO_CHALLENGE_HTML = "<html><body>All good</body></html>"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/133.0.0.0"


class StubSolver:
    """
    A minimal stand-in for :class:`main.CloudflareSolver`.

    ``behaviour`` drives what the page does:

    * ``success`` - the first cookie read already contains cf_clearance
    * ``no_challenge`` - no cookie and no challenge marker
    * ``solve`` - a challenge is present and solving yields the cookie
    * ``timeout`` - the challenge never resolves
    * ``proxy_auth_failure`` - proxy keeps asking for credentials
    """

    instances: List["StubSolver"] = []

    def __init__(
        self,
        *,
        behaviour: str = "success",
        url: str = "https://example.com",
        timeout: float = 30.0,
        cookies: Optional[List[Dict[str, Any]]] = None,
        html: Optional[str] = None,
        request_error: Optional[BaseException] = None,
        start_error: Optional[BaseException] = None,
        factory_error: Optional[BaseException] = None,
        browser_user_agent: str = USER_AGENT,
        **_: Any,
    ) -> None:
        self.behaviour = behaviour
        self.url = url
        self._timeout = timeout
        self._cookies = list(cookies or [])
        self._html = html
        self._request_error = request_error
        self._start_error = start_error
        self._user_agent = browser_user_agent
        self.started = False
        self.stopped = False
        self.solve_calls = 0
        self.solve_started: float = 0.0
        self.click_times: List[float] = []
        self.proxy_failure_reason: Optional[str] = None
        StubSolver.instances.append(self)

        if factory_error is not None:
            raise factory_error

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._start_error is not None:
            raise self._start_error
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    # -- page --------------------------------------------------------------
    async def request_page(self, url: str) -> None:
        self.url = url

        if self._request_error is not None:
            raise self._request_error

    async def get_cookies(self) -> List[Dict[str, Any]]:
        return [dict(cookie) for cookie in self._cookies]

    async def get_user_agent(self) -> str:
        return self._user_agent

    async def set_user_agent_metadata(self, user_agent: str) -> None:
        self._user_agent = user_agent

    async def detect_challenge(self) -> Optional[main.ChallengePlatform]:
        if self.behaviour in ("solve", "timeout"):
            return main.ChallengePlatform.MANAGED

        if self._html is not None:
            for platform in main.ChallengePlatform:
                if f"cType: '{platform.value}'" in self._html:
                    return platform

        return None

    async def solve_challenge(self, timeout: Optional[float] = None) -> bool:
        self.solve_calls += 1

        if self.behaviour == "solve":
            self.click_times.append(time.monotonic())
            self._cookies.append(dict(CLEARANCE_COOKIE))
            return True

        # Never resolves before the timeout: keep "clicking" once per tick.
        deadline = time.monotonic() + (self._timeout if timeout is None else timeout)

        while time.monotonic() < deadline:
            self.click_times.append(time.monotonic())
            await asyncio.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))

        raise main.ChallengeTimeoutError(
            f"Cloudflare challenge was not solved within {self._timeout} seconds"
        )


def make_factory(defaults: Optional[Dict[str, Any]] = None):
    """Build a solver factory that records every created solver."""
    defaults = defaults or {}

    def factory(**kwargs: Any) -> StubSolver:
        StubSolver.instances.clear()
        return StubSolver(**{**defaults, **kwargs})

    return factory


@pytest.fixture(autouse=True)
def _reset_instances() -> None:
    StubSolver.instances.clear()
    yield
    StubSolver.instances.clear()


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# success
# ---------------------------------------------------------------------------


def test_success_returns_structured_result() -> None:
    factory = make_factory(
        {"behaviour": "success", "cookies": [SESSION_COOKIE, CLEARANCE_COOKIE]}
    )
    result = run(
        main.collect_clearance(
            "https://example.com",
            proxy="http://user:pass@proxy:8080",
            user_agent=USER_AGENT,
            solver_factory=factory,
        )
    )

    assert result.status is main.CollectionStatus.SUCCESS
    assert result.succeeded
    assert result.ok
    assert not result.failed
    assert result.error is None
    assert result.url == "https://example.com"
    assert result.proxy == "http://user:pass@proxy:8080"
    assert result.user_agent == USER_AGENT
    assert result.elapsed >= 0
    assert [cookie["name"] for cookie in result.cookies] == [
        "session",
        "cf_clearance",
    ]
    assert result.cf_clearance is not None
    assert result.cf_clearance["value"] == "clearance-value"

    solver = StubSolver.instances[0]
    assert solver.started
    assert solver.stopped


# Reworked: the success path already has a cookie, so no challenge is solved.
def test_success_meta_and_challenge_type_are_none() -> None:
    factory = make_factory({"behaviour": "success", "cookies": [CLEARANCE_COOKIE]})
    result = run(main.collect_clearance("https://example.com", solver_factory=factory))

    assert result.status is main.CollectionStatus.SUCCESS
    assert result.challenge_type is None


# ---------------------------------------------------------------------------
# no challenge
# ---------------------------------------------------------------------------


def test_no_challenge_is_not_a_failure() -> None:
    factory = make_factory({"behaviour": "no_challenge"})
    result = run(main.collect_clearance("https://example.com", solver_factory=factory))

    assert result.status is main.CollectionStatus.NO_CHALLENGE
    assert result.ok
    assert not result.failed
    assert result.cf_clearance is None
    assert result.error is None
    assert result.cookies == []
    assert result.user_agent == USER_AGENT


def test_no_challenge_without_cookies_keeps_partial_results() -> None:
    factory = make_factory({"behaviour": "no_challenge", "cookies": [SESSION_COOKIE]})
    result = run(main.collect_clearance("https://example.com", solver_factory=factory))

    assert result.status is main.CollectionStatus.NO_CHALLENGE
    assert [cookie["name"] for cookie in result.cookies] == ["session"]
    assert result.cf_clearance is None


# ---------------------------------------------------------------------------
# timeout
# ---------------------------------------------------------------------------


def test_challenge_timeout_stops_near_timeout() -> None:
    timeout = 0.5
    factory = make_factory({"behaviour": "timeout"})

    started = time.monotonic()
    result = run(
        main.collect_clearance(
            "https://example.com", timeout=timeout, solver_factory=factory
        )
    )
    duration = time.monotonic() - started

    assert result.status is main.CollectionStatus.TIMEOUT
    assert result.failed
    assert not result.ok
    assert result.challenge_type is main.ChallengePlatform.MANAGED
    assert result.error is not None
    assert "0.5" in result.error

    solver = StubSolver.instances[0]
    assert solver.solve_calls == 1

    # The loop must stop around the deadline rather than run on unbounded.
    assert duration < timeout + 1.5
    assert solver.click_times, "expected the stub to have attempted a click"
    assert solver.click_times[-1] - started <= timeout + 0.5


def test_solver_loop_does_not_click_after_deadline() -> None:
    class ClickTrackingSolver(StubSolver):
        async def solve_challenge(self, timeout: Optional[float] = None) -> bool:
            budget = self._timeout if timeout is None else timeout
            deadline = time.monotonic() + budget
            self.solve_started = time.monotonic()

            while True:
                now = time.monotonic()
                if now >= deadline:
                    break
                self.click_times.append(now)
                await asyncio.sleep(min(0.05, deadline - now))

            raise main.ChallengeTimeoutError("timed out")

    def factory(**kwargs: Any) -> ClickTrackingSolver:
        return ClickTrackingSolver(behaviour="timeout", **kwargs)

    started = time.monotonic()
    result = run(
        main.collect_clearance("https://example.com", timeout=0.4, solver_factory=factory)
    )
    finished = time.monotonic()

    assert result.status is main.CollectionStatus.TIMEOUT
    solver = StubSolver.instances[0]
    assert solver.click_times

    # The loop gets the remaining budget and never clicks past it.
    assert all(moment - solver.solve_started <= 0.4 for moment in solver.click_times)
    assert finished - started < 0.4 + 1.5


# ---------------------------------------------------------------------------
# proxy authentication failure
# ---------------------------------------------------------------------------


def test_proxy_auth_failure_is_reported_as_proxy_error() -> None:
    def factory(**kwargs: Any) -> StubSolver:
        solver = StubSolver(behaviour="timeout", **kwargs)
        solver.proxy_failure_reason = (
            "the proxy rejected the supplied username or password"
        )
        return solver

    result = run(
        main.collect_clearance(
            "https://example.com",
            proxy="http://user:wrong@proxy:8080",
            timeout=1,
            solver_factory=factory,
        )
    )

    assert result.status is main.CollectionStatus.PROXY_ERROR
    assert result.status is not main.CollectionStatus.TIMEOUT
    assert result.error is not None
    assert "proxy" in result.error.lower()
    assert result.proxy == "http://user:wrong@proxy:8080"


def test_proxy_auth_failure_is_not_reported_as_no_challenge() -> None:
    def factory(**kwargs: Any) -> StubSolver:
        solver = StubSolver(behaviour="no_challenge", **kwargs)
        solver.proxy_failure_reason = "proxy authentication failed"
        return solver

    result = run(
        main.collect_clearance(
            "https://example.com",
            proxy="http://user:wrong@proxy:8080",
            solver_factory=factory,
        )
    )

    assert result.status is main.CollectionStatus.PROXY_ERROR


def test_proxy_error_from_page_request_is_classified() -> None:
    factory = make_factory(
        {
            "request_error": RuntimeError(
                "net::ERR_INVALID_AUTH_CREDENTIALS while contacting proxy"
            ),
            "cookies": [SESSION_COOKIE],
        }
    )

    result = run(
        main.collect_clearance(
            "https://example.com",
            proxy="http://user:wrong@proxy:8080",
            solver_factory=factory,
        )
    )

    assert result.status is main.CollectionStatus.PROXY_ERROR
    assert result.error is not None
    # Partial results are preserved.
    assert [cookie["name"] for cookie in result.cookies] == ["session"]


# ---------------------------------------------------------------------------
# browser / page failures become results, never exceptions
# ---------------------------------------------------------------------------


def test_browser_launch_failure_becomes_result() -> None:
    factory = make_factory({"start_error": RuntimeError("chrome not found")})
    result = run(main.collect_clearance("https://example.com", solver_factory=factory))

    assert result.status is main.CollectionStatus.BROWSER_ERROR
    assert result.error is not None
    assert "chrome not found" in result.error

    # Whatever was created is torn down exactly once.
    solver = StubSolver.instances[0]
    assert solver.started is False
    assert solver.stopped is True


def test_solver_constructor_failure_becomes_result() -> None:
    factory = make_factory({"factory_error": RuntimeError("executable missing")})
    result = run(main.collect_clearance("https://example.com", solver_factory=factory))

    assert result.status is main.CollectionStatus.BROWSER_ERROR
    assert result.error is not None
    assert "executable missing" in result.error


def test_page_request_failure_becomes_result_with_partial_cookies() -> None:
    factory = make_factory(
        {"request_error": RuntimeError("navigation failed"), "cookies": [SESSION_COOKIE]}
    )
    result = run(main.collect_clearance("https://example.com", solver_factory=factory))

    assert result.status is main.CollectionStatus.PAGE_ERROR
    assert result.error is not None
    assert "navigation failed" in result.error
    assert [cookie["name"] for cookie in result.cookies] == ["session"]


def test_page_request_failure_keeps_clearance_if_already_obtained() -> None:
    factory = make_factory(
        {"request_error": RuntimeError("boom"), "cookies": [CLEARANCE_COOKIE]}
    )
    result = run(main.collect_clearance("https://example.com", solver_factory=factory))

    assert result.status is main.CollectionStatus.SUCCESS
    assert result.error is None


def test_detection_failure_becomes_result() -> None:
    class DetectionFailure(StubSolver):
        async def detect_challenge(self) -> Optional[main.ChallengePlatform]:
            raise RuntimeError("dom unavailable")

    def factory(**kwargs: Any) -> DetectionFailure:
        return DetectionFailure(**kwargs)

    result = run(main.collect_clearance("https://example.com", solver_factory=factory))

    assert result.status is main.CollectionStatus.ERROR
    assert result.error is not None
    assert "dom unavailable" in result.error


# ---------------------------------------------------------------------------
# result model
# ---------------------------------------------------------------------------


def test_result_serialises_to_json_friendly_dict() -> None:
    factory = make_factory({"behaviour": "success", "cookies": [CLEARANCE_COOKIE]})
    result = run(
        main.collect_clearance(
            "https://example.com",
            proxy="http://proxy:8080",
            user_agent=USER_AGENT,
            solver_factory=factory,
        )
    )

    data = result.to_dict()

    assert data["status"] == "success"
    assert data["url"] == "https://example.com"
    assert data["proxy"] == "http://proxy:8080"
    assert data["user_agent"] == USER_AGENT
    assert data["cf_clearance"]["name"] == "cf_clearance"
    assert isinstance(data["elapsed"], float)
    assert data["challenge_type"] is None
    assert json.loads(json.dumps(data))["status"] == "success"


def test_status_helpers_distinguish_success_from_failure() -> None:
    assert not main.CollectionStatus.SUCCESS.is_failure
    assert not main.CollectionStatus.NO_CHALLENGE.is_failure
    assert main.CollectionStatus.TIMEOUT.is_failure
    assert main.CollectionStatus.PROXY_ERROR.is_failure
    assert main.CollectionStatus.BROWSER_ERROR.is_failure


def test_extract_clearance_cookie() -> None:
    assert main.extract_clearance_cookie([SESSION_COOKIE]) is None
    extracted = main.extract_clearance_cookie([SESSION_COOKIE, CLEARANCE_COOKIE])
    assert extracted is not None
    assert extracted["value"] == "clearance-value"


# ---------------------------------------------------------------------------
# command line entry point
# ---------------------------------------------------------------------------


def test_cli_prints_same_status_as_interface(capsys, monkeypatch) -> None:
    factory = make_factory({"behaviour": "success", "cookies": [CLEARANCE_COOKIE]})
    monkeypatch.setattr(main, "CloudflareSolver", factory)

    exit_code = run(main.main(["--json", "https://example.com"]))
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == main.CollectionStatus.SUCCESS.value
    assert payload["cf_clearance"]["value"] == "clearance-value"


def test_cli_reports_no_challenge_without_failing(capsys, monkeypatch) -> None:
    factory = make_factory({"behaviour": "no_challenge"})
    monkeypatch.setattr(main, "CloudflareSolver", factory)

    exit_code = run(main.main(["--json", "https://example.com"]))
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == main.CollectionStatus.NO_CHALLENGE.value
    assert payload["cf_clearance"] is None


def test_cli_timeout_reports_failure_status(capsys, monkeypatch) -> None:
    factory = make_factory({"behaviour": "timeout"})
    monkeypatch.setattr(main, "CloudflareSolver", factory)

    exit_code = run(main.main(["--json", "-t", "0.3", "https://example.com"]))
    captured = capsys.readouterr()

    assert exit_code == 1
    payload = json.loads(captured.out)
    assert payload["status"] == main.CollectionStatus.TIMEOUT.value
    assert payload["challenge_type"] == main.ChallengePlatform.MANAGED.value
    assert payload["error"]


def test_solver_reporting_false_without_cookie_yields_no_challenge() -> None:
    class UnsolvedChallenge(StubSolver):
        async def solve_challenge(self, timeout: Optional[float] = None) -> bool:
            # The challenge widget disappeared, but no cookie was issued.
            return False

        async def detect_challenge(self) -> Optional[main.ChallengePlatform]:
            if self.solve_calls:
                return None
            self.solve_calls += 1
            return main.ChallengePlatform.INTERACTIVE

    def factory(**kwargs: Any) -> UnsolvedChallenge:
        return UnsolvedChallenge(**kwargs)

    result = run(main.collect_clearance("https://example.com", solver_factory=factory))

    assert result.status is main.CollectionStatus.NO_CHALLENGE
    assert result.challenge_type is main.ChallengePlatform.INTERACTIVE
    assert result.error is None


def test_solver_reporting_true_without_cookie_is_an_error() -> None:
    class LyingSolver(StubSolver):
        async def solve_challenge(self, timeout: Optional[float] = None) -> bool:
            return True

    def factory(**kwargs: Any) -> LyingSolver:
        return LyingSolver(behaviour="timeout", **kwargs)

    result = run(main.collect_clearance("https://example.com", solver_factory=factory))

    assert result.status is main.CollectionStatus.ERROR
    assert result.error is not None
    assert "disappeared" in result.error
