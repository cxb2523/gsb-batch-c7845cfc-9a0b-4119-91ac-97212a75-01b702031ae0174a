"""
The real solver loop and CDP proxy-authentication flow, driven by stubs.

These tests exercise the production code paths in ``main.CloudflareSolver``
(the click loop, the deadline handling and the ``Fetch.authRequired`` handler)
without launching a browser.
"""

from __future__ import annotations

import asyncio
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

CHALLENGE_HTML = "<html><script>cType: 'managed'</script></html>"
NO_CHALLENGE_HTML = "<html><body>All good</body></html>"

run = asyncio.run


class FakeElementNode:
    """Minimal node returned by the stubbed ``Tab.find``."""

    def __init__(self) -> None:
        self.shadow_roots = [object()]
        self.children = [object()]
        self.tree = object()
        self.attrs: Dict[str, str] = {}
        self.parent: Optional["FakeElementNode"] = None


def make_widget_input(parent: bool = True) -> Any:
    """Build a fake ``<input>`` element with the given parent shadow root."""
    widget = FakeElementNode()

    if parent:
        parent_node = FakeElementNode()
        widget.parent = parent_node

    return widget


class FakeElement:
    """Stand-in for :class:`main.Element` used by the solver's click path."""

    def __init__(self, node: Any, tab: Any, tree: Any = None) -> None:
        self.node = node
        self.tab = tab
        self.attrs = {"style": ""}
        self.children = [self]

    async def get_position(self) -> None:
        return None

    async def mouse_click(self) -> None:
        self.tab.clicks.append(time.monotonic())


class FakeTab:
    def __init__(self, *, html: str, find_delay: float = 0.0) -> None:
        self.html = html
        self.clicks: List[float] = []
        self.find_delay = find_delay

    async def get_content(self) -> str:
        return self.html

    async def find(self, text: str, **_: Any) -> FakeElementNode:
        if self.find_delay:
            await asyncio.sleep(self.find_delay)
        return make_widget_input()

    async def send(self, command: Any) -> None:
        next(command)

    def add_handler(self, *_: Any, **__: Any) -> None:
        return None

    def feed_cdp(self, *_: Any, **__: Any) -> None:
        return None

    def evaluate(self, *_: Any, **__: Any) -> str:
        return ""


class FakeCookies:
    def __init__(self, cookies: List[Dict[str, Any]]) -> None:
        self._cookies = cookies

    async def get_all(self) -> List[Any]:
        class Cookie:
            def __init__(self, payload: Dict[str, Any]) -> None:
                self.payload = payload

            def to_json(self) -> Dict[str, Any]:
                return dict(self.payload)

        return [Cookie(cookie) for cookie in self._cookies]


class FakeDriver:
    def __init__(self, tab: FakeTab, cookies: Optional[List[Dict[str, Any]]] = None):
        self.main_tab = tab
        self.cookies = FakeCookies(cookies or [])


def build_solver(timeout: float, tab: FakeTab) -> main.CloudflareSolver:
    """Create a solver whose browser is the stub above."""
    solver = object.__new__(main.CloudflareSolver)
    solver.driver = FakeDriver(tab)
    solver._timeout = timeout
    solver.proxy_failure_reason = None
    solver._proxy_auth_requests = set()
    solver._navigation_errors = []
    return solver


# ---------------------------------------------------------------------------
# the real clicking loop obeys the timeout
# ---------------------------------------------------------------------------


def test_real_solver_loop_stops_at_timeout_and_never_clicks_after(monkeypatch) -> None:
    monkeypatch.setattr(main, "Element", FakeElement)
    tab = FakeTab(html=CHALLENGE_HTML)
    timeout = 2.2
    solver = build_solver(timeout, tab)

    started = time.monotonic()

    with pytest.raises((main.ChallengeTimeoutError, asyncio.TimeoutError)):
        run(solver.solve_challenge(timeout=timeout))

    finished = time.monotonic()
    deadline = started + timeout

    assert tab.clicks, "the solver should have attempted at least one click"
    assert all(click <= deadline for click in tab.clicks)
    assert finished < deadline + 0.5


def test_real_solver_stops_quickly_for_short_timeouts(monkeypatch) -> None:
    monkeypatch.setattr(main, "Element", FakeElement)
    tab = FakeTab(html=CHALLENGE_HTML)
    solver = build_solver(0.3, tab)

    started = time.monotonic()

    with pytest.raises(main.ChallengeTimeoutError):
        run(solver.solve_challenge())

    assert time.monotonic() - started < 0.3 + 0.5


def test_real_solver_returns_true_when_cookie_appears(monkeypatch) -> None:
    monkeypatch.setattr(main, "Element", FakeElement)
    tab = FakeTab(html=CHALLENGE_HTML)
    solver = build_solver(5.0, tab)
    solver.driver = FakeDriver(tab, cookies=[CLEARANCE_COOKIE])

    assert run(solver.solve_challenge(timeout=5.0)) is True
    assert tab.clicks == []


def test_real_solver_returns_false_when_challenge_disappears(monkeypatch) -> None:
    monkeypatch.setattr(main, "Element", FakeElement)
    tab = FakeTab(html=NO_CHALLENGE_HTML)
    solver = build_solver(5.0, tab)

    assert run(solver.solve_challenge(timeout=5.0)) is False
    assert tab.clicks == []


def test_real_solver_detects_challenge_platform() -> None:
    solver = build_solver(5.0, FakeTab(html=CHALLENGE_HTML))
    assert run(solver.detect_challenge()) is main.ChallengePlatform.MANAGED

    solver = build_solver(5.0, FakeTab(html=NO_CHALLENGE_HTML))
    assert run(solver.detect_challenge()) is None


# ---------------------------------------------------------------------------
# proxy authentication still goes through the CDP Fetch flow
# ---------------------------------------------------------------------------


class FakeAuthTab:
    def __init__(self) -> None:
        self.responses: List[Any] = []

    async def send(self, command: Any) -> None:
        message = next(command)
        self.responses.append(message["params"]["authChallengeResponse"])


def make_auth_event(source: str = "Proxy", request_id: str = "request-1") -> Any:
    class AuthChallenge:
        def __init__(self, source: str) -> None:
            self.source = source

    class Event:
        pass

    Event.request_id = main.cdp.fetch.RequestId(request_id)
    Event.auth_challenge = AuthChallenge(source)
    return Event()


def make_proxy(**overrides: Any) -> main.Proxy:
    values = {"scheme": "http", "host": "proxy", "port": 8080}
    values.update(overrides)
    return main.Proxy(**values)


def test_proxy_auth_uses_configured_credentials() -> None:
    solver = build_solver(5.0, FakeTab(html=NO_CHALLENGE_HTML))
    auth_tab = FakeAuthTab()
    solver.driver = FakeDriver(auth_tab)
    solver._proxy = make_proxy(username="user", password="pass")

    run(solver._on_auth_required(make_auth_event()))

    assert auth_tab.responses == [
        {"response": "ProvideCredentials", "username": "user", "password": "pass"}
    ]
    assert solver.proxy_failure_reason is None


def test_proxy_auth_rejection_is_recorded_and_cancelled() -> None:
    solver = build_solver(5.0, FakeTab(html=NO_CHALLENGE_HTML))
    auth_tab = FakeAuthTab()
    solver.driver = FakeDriver(auth_tab)
    solver._proxy = make_proxy(username="user", password="wrong")

    # Chrome asks twice for the same request when the credentials are refused.
    run(solver._on_auth_required(make_auth_event(request_id="request-1")))
    run(solver._on_auth_required(make_auth_event(request_id="request-1")))

    assert auth_tab.responses[0]["response"] == "ProvideCredentials"
    assert auth_tab.responses[1]["response"] == "CancelAuth"
    assert solver.proxy_failure_reason is not None
    assert "rejected" in solver.proxy_failure_reason


def test_proxy_auth_for_distinct_requests_is_not_a_failure() -> None:
    solver = build_solver(5.0, FakeTab(html=NO_CHALLENGE_HTML))
    auth_tab = FakeAuthTab()
    solver.driver = FakeDriver(auth_tab)
    solver._proxy = make_proxy(username="user", password="correct")

    # Different requests legitimately each ask once.
    run(solver._on_auth_required(make_auth_event(request_id="request-1")))
    run(solver._on_auth_required(make_auth_event(request_id="request-2")))
    run(solver._on_auth_required(make_auth_event(request_id="request-3")))

    assert [entry["response"] for entry in auth_tab.responses] == [
        "ProvideCredentials",
        "ProvideCredentials",
        "ProvideCredentials",
    ]
    assert solver.proxy_failure_reason is None


def test_proxy_auth_without_credentials_is_a_proxy_failure() -> None:
    solver = build_solver(5.0, FakeTab(html=NO_CHALLENGE_HTML))
    auth_tab = FakeAuthTab()
    solver.driver = FakeDriver(auth_tab)
    solver._proxy = make_proxy()

    run(solver._on_auth_required(make_auth_event()))

    assert auth_tab.responses == [{"response": "CancelAuth"}]
    assert solver.proxy_failure_reason is not None


def test_non_proxy_auth_challenge_is_deferred() -> None:
    solver = build_solver(5.0, FakeTab(html=NO_CHALLENGE_HTML))
    auth_tab = FakeAuthTab()
    solver.driver = FakeDriver(auth_tab)

    run(solver._on_auth_required(make_auth_event(source="Server")))

    assert auth_tab.responses == [{"response": "Default"}]
    assert solver.proxy_failure_reason is None


def test_proxy_navigation_failure_is_recorded() -> None:
    solver = build_solver(5.0, FakeTab(html=NO_CHALLENGE_HTML))
    solver._proxy = make_proxy(username="user", password="pass")

    class Event:
        error_text = "net::ERR_INVALID_AUTH_CREDENTIALS"

    run(solver._on_loading_failed(Event()))

    assert solver.proxy_failure_reason is not None
    assert solver._navigation_errors == ["net::ERR_INVALID_AUTH_CREDENTIALS"]
