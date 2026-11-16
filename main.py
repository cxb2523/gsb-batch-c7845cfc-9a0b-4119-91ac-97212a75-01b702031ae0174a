from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, Final, Iterable, List, Optional
from urllib.parse import urlparse

import chrome_version
import requests
import user_agents
import zendriver
from zendriver import cdp
from zendriver.cdp.emulation import UserAgentBrandVersion, UserAgentMetadata
from zendriver.cdp.fetch import AuthChallengeResponse, AuthRequired, RequestPaused
from zendriver.cdp.network import Cookie, LoadingFailed, T_JSON_DICT
from zendriver.core.element import Element

logger = logging.getLogger(__name__)

COMMAND: Final[str] = (
    '{name}: {binary} --header "Cookie: {cookies}" --header "User-Agent: {user_agent}" {url}'
)

#: Chrome net errors that point at the proxy rather than at the target page.
_PROXY_ERROR_MARKERS: Final[tuple] = (
    "ERR_PROXY",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_INVALID_AUTH_CREDENTIALS",
    "ERR_UNEXPECTED_PROXY_AUTH",
    "PROXY_AUTH",
    "PROXY AUTHENTICATION REQUIRED",
    "PROXY AUTHENTICATION FAILED",
)


def get_chrome_user_agent() -> str:
    """
    Get a random up-to-date Chrome user agent string.

    Returns
    -------
    str
        The user agent string.
    """
    full_version = chrome_version.get_chrome_version()

    if full_version is None:
        response = requests.get(
            "https://versionhistory.googleapis.com/v1/chrome/platforms/all/channels/stable/versions",
            params={"order_by": "version desc", "page_size": 1},
            timeout=10,
        )

        response_json = response.json()
        full_version = response_json["versions"][0]["version"]

    major_version = int(full_version.split(".")[0])

    user_agent_list = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version}.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version}.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version}.0.0.0 Safari/537.36",
    ]

    return random.choice(user_agent_list).format(version=major_version)


class ChallengePlatform(str, Enum):
    """Cloudflare challenge platform types."""

    JAVASCRIPT = "non-interactive"
    MANAGED = "managed"
    INTERACTIVE = "interactive"

    @property
    def label(self) -> str:
        """Human readable name of the challenge platform."""
        return _PLATFORM_LABELS[self]


#: Human readable names used for logging.
_PLATFORM_LABELS: Final[Dict[ChallengePlatform, str]] = {
    ChallengePlatform.JAVASCRIPT: "JavaScript",
    ChallengePlatform.MANAGED: "Managed",
    ChallengePlatform.INTERACTIVE: "Interactive",
}


class CollectionStatus(str, Enum):
    """
    Outcome of a clearance collection attempt.

    ``SUCCESS`` and ``NO_CHALLENGE`` are both successful outcomes: the page
    either handed out a ``cf_clearance`` cookie or never issued a challenge in
    the first place. Every other status describes a failure.
    """

    SUCCESS = "success"
    NO_CHALLENGE = "no_challenge"
    TIMEOUT = "timeout"
    PROXY_ERROR = "proxy_error"
    BROWSER_ERROR = "browser_error"
    PAGE_ERROR = "page_error"
    ERROR = "error"

    @property
    def is_failure(self) -> bool:
        """Whether this status represents a failed collection attempt."""
        return self not in (CollectionStatus.SUCCESS, CollectionStatus.NO_CHALLENGE)


#: Convenience alias; different callers describe the same concept differently.
SolveStatus = CollectionStatus

class ChallengeTimeoutError(asyncio.TimeoutError):
    """
    Raised when a Cloudflare challenge could not be solved within the timeout.

    Inherits from :class:`asyncio.TimeoutError` so callers that already catch
    the latter keep working.
    """


@dataclass
class CollectionResult:
    """
    Structured result of :func:`collect_clearance`.

    Attributes
    ----------
    status : CollectionStatus
        The outcome of the attempt. ``success`` means a ``cf_clearance`` cookie
        was obtained, ``no_challenge`` means the page issued no challenge and
        no clearance cookie, every other value is a failure.
    url : str
        The URL that was requested.
    challenge_type : Optional[ChallengePlatform]
        The detected Cloudflare challenge platform, if any.
    cookies : List[T_JSON_DICT]
        All cookies that were present when the attempt finished. This also
        contains partial results when the attempt failed.
    cf_clearance : Optional[T_JSON_DICT]
        The extracted ``cf_clearance`` cookie, if one was obtained.
    user_agent : Optional[str]
        The user agent of the browser that obtained the cookies. It is always
        taken from the browser itself so it stays consistent with the cookies.
    proxy : Optional[str]
        The proxy URL that was used, if any.
    elapsed : float
        Wall clock duration of the whole attempt in seconds.
    error : Optional[str]
        Human readable failure reason. ``None`` for successful outcomes.
    """

    status: CollectionStatus
    url: str
    challenge_type: Optional[ChallengePlatform] = None
    cookies: List[T_JSON_DICT] = field(default_factory=list)
    cf_clearance: Optional[T_JSON_DICT] = None
    user_agent: Optional[str] = None
    proxy: Optional[str] = None
    elapsed: float = 0.0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """Whether the attempt was successful (success or no challenge)."""
        return not self.status.is_failure

    @property
    def succeeded(self) -> bool:
        """Whether a ``cf_clearance`` cookie was obtained."""
        return self.status is CollectionStatus.SUCCESS

    @property
    def failed(self) -> bool:
        """Whether the attempt failed."""
        return self.status.is_failure

    @property
    def error_reason(self) -> Optional[str]:
        """Alias for :attr:`error`."""
        return self.error

    @property
    def failure_reason(self) -> Optional[str]:
        """Alias for :attr:`error`."""
        return self.error

    @property
    def duration(self) -> float:
        """Alias for :attr:`elapsed`."""
        return self.elapsed

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON serialisable representation of the result."""
        return {
            "status": self.status.value,
            "url": self.url,
            "challenge_type": (
                self.challenge_type.value if self.challenge_type is not None else None
            ),
            "cookies": list(self.cookies),
            "cf_clearance": self.cf_clearance,
            "user_agent": self.user_agent,
            "proxy": self.proxy,
            "elapsed": round(self.elapsed, 3),
            "error": self.error,
        }


@dataclass
class Proxy:
    """A class representing a proxy server."""

    scheme: str
    host: str
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None

    @classmethod
    def from_url(cls, proxy_url: str) -> Proxy:
        """
        Create a Proxy instance from a proxy URL.

        Parameters
        ----------
        proxy_url : str
            The proxy server URL.

        Returns
        -------
        Proxy
            The Proxy instance.
        """
        parsed = urlparse(proxy_url)

        return cls(
            scheme=parsed.scheme,
            host=parsed.hostname or "",
            port=parsed.port,
            username=parsed.username,
            password=parsed.password,
        )

    @property
    def url(self) -> str:
        """
        Get the proxy URL without authentication.

        Returns
        -------
        str
            The proxy URL.
        """
        host = self.host

        if self.port:
            host += f":{self.port}"

        return f"{self.scheme}://{host}"


class CloudflareSolver:
    """
    A class for solving Cloudflare challenges with Zendriver.

    Parameters
    ----------
    user_agent : Optional[str]
        The user agent string to use for the browser requests.
    timeout : float
        The timeout in seconds to use for browser actions and solving challenges.
    http2 : bool
        Enable or disable the usage of HTTP/2 for the browser requests.
    http3 : bool
        Enable or disable the usage of HTTP/3 for the browser requests.
    headless : bool
        Enable or disable headless mode for the browser (not supported on Windows).
    proxy : Optional[str]
        The proxy server URL to use for the browser requests.
    """

    def __init__(
        self,
        *,
        user_agent: Optional[str],
        timeout: float,
        http2: bool,
        http3: bool,
        headless: bool,
        proxy: Optional[str],
    ) -> None:
        config = zendriver.Config(headless=headless)

        if user_agent is not None:
            config.add_argument(f"--user-agent={user_agent}")

        if not http2:
            config.add_argument("--disable-http2")

        if not http3:
            config.add_argument("--disable-quic")

        self._proxy = Proxy.from_url(proxy) if proxy is not None else None

        if self._proxy is not None:
            config.add_argument(f"--proxy-server={self._proxy.url}")

        self.driver = zendriver.Browser(config)
        self._timeout = timeout

        #: Human readable reason for a proxy related failure, if one was seen.
        self.proxy_failure_reason: Optional[str] = None
        self._proxy_auth_requests: set = set()
        self._navigation_errors: List[str] = []

    async def __aenter__(self) -> CloudflareSolver:
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        """Start the underlying browser."""
        await self.driver.start()

    async def stop(self) -> None:
        """Stop the underlying browser."""
        await self.driver.stop()

    async def _send_auth_response(
        self, request_id: Any, response: AuthChallengeResponse
    ) -> None:
        """Send an authentication response for a paused request."""
        await self.driver.main_tab.send(
            cdp.fetch.continue_with_auth(request_id, response)
        )

    async def _on_auth_required(self, event: AuthRequired) -> None:
        """
        Handle authentication requests for the proxy server.

        The credentials are supplied through the same CDP ``Fetch`` flow as
        before. Chrome re-issues ``Fetch.authRequired`` for a single request
        when the proxy refuses the credentials that were supplied, so a second
        event for the same request is what marks the authentication failure.
        The failure is recorded so the attempt can be reported as a proxy error
        instead of a plain timeout.

        Parameters
        ----------
        event : AuthRequired
            The authentication required event.
        """
        if event.auth_challenge.source != "Proxy":
            try:
                await self.driver.main_tab.send(
                    cdp.fetch.continue_with_auth(
                        event.request_id, AuthChallengeResponse(response="Default")
                    )
                )
            except Exception as error:
                logger.debug("Could not continue a non-proxy auth request: %s", error)
            return

        request_key = str(event.request_id)
        already_asked = request_key in self._proxy_auth_requests
        self._proxy_auth_requests.add(request_key)

        if self._proxy is None or self._proxy.username is None:
            self.proxy_failure_reason = (
                "proxy authentication required but no credentials were configured"
            )

            try:
                await self._send_auth_response(
                    event.request_id, AuthChallengeResponse(response="CancelAuth")
                )
            except Exception as error:
                logger.debug("Could not cancel the proxy auth request: %s", error)
            return

        if already_asked:
            # Chrome asks again only after the credentials we supplied were
            # refused, so stop retrying and report the failure.
            self.proxy_failure_reason = (
                "the proxy rejected the supplied username or password"
            )

            try:
                await self._send_auth_response(
                    event.request_id, AuthChallengeResponse(response="CancelAuth")
                )
            except Exception as error:
                logger.debug("Could not cancel the proxy auth request: %s", error)
            return

        try:
            await self._send_auth_response(
                event.request_id,
                AuthChallengeResponse(
                    response="ProvideCredentials",
                    username=self._proxy.username,
                    password=self._proxy.password,
                ),
            )
        except Exception as error:  # handlers must never break the event loop
            self.proxy_failure_reason = f"could not supply proxy credentials: {error}"

    async def _on_loading_failed(self, event: LoadingFailed) -> None:
        """
        Record navigation failures, keeping track of proxy related ones.

        Parameters
        ----------
        event : LoadingFailed
            The loading failed event.
        """
        error_text = str(getattr(event, "error_text", "") or "")
        self._navigation_errors.append(error_text)

        if self._proxy is None:
            return

        upper = error_text.upper()
        if any(marker in upper for marker in _PROXY_ERROR_MARKERS):
            self.proxy_failure_reason = f"proxy request failed: {error_text}"

    async def _continue_request(self, event: RequestPaused) -> None:
        """
        Continue a paused request.

        Parameters
        ----------
        event : RequestPaused
            The request paused event.
        """
        await self.driver.main_tab.send(
            cdp.fetch.continue_request(request_id=event.request_id)
        )

    @staticmethod
    def _format_cookies(cookies: Iterable[Cookie]) -> List[T_JSON_DICT]:
        """
        Format cookies into a list of JSON cookies.

        Parameters
        ----------
        cookies : Iterable[Cookie]
            List of cookies.

        Returns
        -------
        List[T_JSON_DICT]
            List of JSON cookies.
        """
        return [cookie.to_json() for cookie in cookies]

    @staticmethod
    def extract_clearance_cookie(
        cookies: Iterable[T_JSON_DICT],
    ) -> Optional[T_JSON_DICT]:
        """
        Extract the Cloudflare clearance cookie from a list of cookies.

        Parameters
        ----------
        cookies : Iterable[T_JSON_DICT]
            List of cookies.

        Returns
        -------
        Optional[T_JSON_DICT]
            The Cloudflare clearance cookie. Returns None if the cookie is not found.
        """
        return extract_clearance_cookie(cookies)

    async def get_user_agent(self) -> str:
        """
        Get the current user agent string.

        Returns
        -------
        str
            The user agent string.
        """
        return await self.driver.main_tab.evaluate("navigator.userAgent")

    async def get_cookies(self) -> List[T_JSON_DICT]:
        """
        Get all cookies from the current page.

        Returns
        -------
        List[T_JSON_DICT]
            List of cookies.
        """
        return self._format_cookies(await self.driver.cookies.get_all())

    async def set_user_agent_metadata(self, user_agent: str) -> None:
        """
        Set the user agent metadata for the browser.

        Parameters
        ----------
        user_agent : str
            The user agent string to parse information from.
        """
        device = user_agents.parse(user_agent)

        metadata = UserAgentMetadata(
            architecture="x86",
            bitness="64",
            brands=[
                UserAgentBrandVersion(brand="Not)A;Brand", version="8"),
                UserAgentBrandVersion(
                    brand="Chromium", version=str(device.browser.version[0])
                ),
                UserAgentBrandVersion(
                    brand="Google Chrome",
                    version=str(device.browser.version[0]),
                ),
            ],
            full_version_list=[
                UserAgentBrandVersion(brand="Not)A;Brand", version="8"),
                UserAgentBrandVersion(
                    brand="Chromium", version=str(device.browser.version[0])
                ),
                UserAgentBrandVersion(
                    brand="Google Chrome",
                    version=str(device.browser.version[0]),
                ),
            ],
            mobile=device.is_mobile,
            model=device.device.model or "",
            platform=device.os.family,
            platform_version=device.os.version_string,
            full_version=device.browser.version_string,
            wow64=False,
        )

        self.driver.main_tab.feed_cdp(
            cdp.network.set_user_agent_override(
                user_agent, user_agent_metadata=metadata
            )
        )

    async def request_page(self, url: str) -> None:
        """
        Request a page with the browser,
        setting up handlers for proxy authentication if a proxy is being used.

        Parameters
        ----------
        url : str
            The URL of the page to request.
        """
        main_tab = self.driver.main_tab

        if self._proxy is not None:
            main_tab.add_handler(LoadingFailed, self._on_loading_failed)
            await self.driver.get()
            main_tab.add_handler(AuthRequired, self._on_auth_required)
            main_tab.add_handler(RequestPaused, self._continue_request)
            main_tab.feed_cdp(cdp.fetch.enable(handle_auth_requests=True))

        await self.driver.get(url)

    async def detect_challenge(self) -> Optional[ChallengePlatform]:
        """
        Detect the Cloudflare challenge platform on the current page.

        Returns
        -------
        Optional[ChallengePlatform]
            The Cloudflare challenge platform.
        """
        html = await self.driver.main_tab.get_content()

        for platform in ChallengePlatform:
            if f"cType: '{platform.value}'" in html:
                return platform

        return None

    @staticmethod
    def _now() -> float:
        """Monotonic clock used for deadline tracking."""
        return time.monotonic()

    def _timeout_message(self, timeout: Optional[float] = None) -> str:
        """Build the timeout failure reason."""
        seconds = self._timeout if timeout is None else timeout

        return f"Cloudflare challenge was not solved within {seconds} seconds"

    def _ensure_before(
        self, deadline: float, timeout: Optional[float] = None
    ) -> None:
        """Raise :class:`ChallengeTimeoutError` when ``deadline`` has passed."""
        if self._now() >= deadline:
            raise ChallengeTimeoutError(self._timeout_message(timeout))

    async def _run_until(
        self,
        factory: Callable[[float], Any],
        deadline: float,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        """
        Run ``factory(remaining)`` and await its result, bounded by ``deadline``.

        ``factory`` receives the number of seconds left and is therefore never
        called once the deadline has passed, which keeps awaits - and, more
        importantly, clicks - from starting after the timeout.

        Raises
        ------
        ChallengeTimeoutError
            When the deadline is reached.
        """
        remaining = deadline - self._now()

        if remaining <= 0:
            raise ChallengeTimeoutError(self._timeout_message(timeout))

        outcome = factory(remaining)

        if not inspect.isawaitable(outcome):
            return outcome

        try:
            return await asyncio.wait_for(outcome, remaining)
        except ChallengeTimeoutError:
            raise
        except asyncio.TimeoutError as error:
            if self._now() >= deadline:
                raise ChallengeTimeoutError(self._timeout_message(timeout)) from error
            # The inner operation timed out on its own, before our deadline.
            raise asyncio.TimeoutError(str(error) or "operation timed out") from error

    async def _sleep_until(
        self, deadline: float, seconds: float, *, timeout: Optional[float] = None
    ) -> None:
        """Sleep, but never past ``deadline``."""
        remaining = deadline - self._now()

        if remaining <= 0:
            raise ChallengeTimeoutError(self._timeout_message(timeout))

        await asyncio.sleep(min(seconds, remaining))
        self._ensure_before(deadline, timeout)

    async def _click_challenge_button(
        self, deadline: float, *, timeout: Optional[float] = None
    ) -> bool:
        """
        Locate the challenge widget and click it, if it is visible.

        The whole lookup, waiting and clicking is bounded by ``deadline``; a
        click is never initiated once the deadline has passed.

        Returns
        -------
        bool
            Whether a click was issued.
        """
        try:
            widget_input = await self._run_until(
                lambda _: self.driver.main_tab.find("input"),
                deadline,
                timeout=timeout,
            )
        except ChallengeTimeoutError:
            raise
        except asyncio.TimeoutError:
            # The lookup gave up before the deadline; keep looking.
            await self._sleep_until(deadline, 0.25, timeout=timeout)
            return False

        parent = widget_input.parent

        if parent is None or not parent.shadow_roots:
            await self._sleep_until(deadline, 0.25, timeout=timeout)
            return False

        challenge = Element(parent.shadow_roots[0], self.driver.main_tab, parent.tree)

        if not challenge.children:
            await self._sleep_until(deadline, 0.25, timeout=timeout)
            return False

        challenge = challenge.children[0]

        if not isinstance(challenge, Element):
            await self._sleep_until(deadline, 0.25, timeout=timeout)
            return False

        style = challenge.attrs.get("style") or ""

        if "display: none;" in style:
            await self._sleep_until(deadline, 0.25, timeout=timeout)
            return False

        await self._sleep_until(deadline, 1.0, timeout=timeout)

        try:
            await self._run_until(
                lambda _: challenge.get_position(), deadline, timeout=timeout
            )
        except (ChallengeTimeoutError, asyncio.TimeoutError):
            raise
        except Exception:
            # Element cannot be located; try again on the next iteration.
            return False

        # Never start a click once the timeout has been reached.
        self._ensure_before(deadline, timeout)
        await self._run_until(
            lambda _: challenge.mouse_click(), deadline, timeout=timeout
        )
        return True

    async def solve_challenge(self, timeout: Optional[float] = None) -> bool:
        """
        Solve the Cloudflare challenge on the current page.

        Parameters
        ----------
        timeout : Optional[float]
            The maximum number of seconds to spend solving. Defaults to the
            timeout this solver was created with.

        Returns
        -------
        bool
            ``True`` when a clearance cookie was obtained, ``False`` when the
            challenge disappeared without one.

        Raises
        ------
        ChallengeTimeoutError
            When the timeout was reached before a cookie was obtained. No click
            is issued after the deadline.
        """
        timeout = self._timeout if timeout is None else timeout
        deadline = self._now() + max(float(timeout), 0.0)
        self._ensure_before(deadline, timeout)

        while True:
            cookies = await self._run_until(
                lambda _: self.get_cookies(), deadline, timeout=timeout
            )

            if extract_clearance_cookie(cookies) is not None:
                return True

            try:
                platform = await self._run_until(
                    lambda _: self.detect_challenge(), deadline, timeout=timeout
                )
            except ChallengeTimeoutError:
                raise
            except asyncio.TimeoutError:
                # a transient lookup timeout; keep trying while time is left
                await self._sleep_until(deadline, 0.25, timeout=timeout)
                continue

            if platform is None:
                return False

            try:
                await self._click_challenge_button(deadline, timeout=timeout)
            except ChallengeTimeoutError:
                raise
            except asyncio.TimeoutError:
                # A lookup timed out on its own; let the deadline decide whether
                # the attempt is over or another iteration may run.
                await self._sleep_until(deadline, 0.25, timeout=timeout)


def extract_clearance_cookie(
    cookies: Iterable[T_JSON_DICT],
) -> Optional[T_JSON_DICT]:
    """
    Extract the Cloudflare clearance cookie from a list of cookies.

    Parameters
    ----------
    cookies : Iterable[T_JSON_DICT]
        List of cookies.

    Returns
    -------
    Optional[T_JSON_DICT]
        The Cloudflare clearance cookie. Returns None if the cookie is not found.
    """
    for cookie in cookies:
        if cookie.get("name") == "cf_clearance":
            return cookie

    return None


def _describe_error(error: BaseException) -> str:
    """Format an exception as a human readable failure reason."""
    message = str(error).strip()
    name = type(error).__name__

    return f"{name}: {message}" if message else name


def _looks_like_proxy_error(message: str) -> bool:
    """Whether a failure message points at the proxy rather than the page."""
    upper = message.upper()

    return any(marker in upper for marker in _PROXY_ERROR_MARKERS)


async def _capture_state(
    solver: Any, result: CollectionResult, *, timeout: float = 5.0
) -> None:
    """
    Best effort collection of cookies and the browser user agent.

    Used to keep partial results when something failed, so it never raises and
    never blocks the caller for longer than ``timeout`` per lookup.
    """
    try:
        cookies = await asyncio.wait_for(solver.get_cookies(), timeout)
        result.cookies = list(cookies)
        result.cf_clearance = extract_clearance_cookie(result.cookies)
    except Exception as error:
        logger.debug("Could not read cookies: %s", error)

    try:
        user_agent = await asyncio.wait_for(solver.get_user_agent(), timeout)

        if user_agent:
            result.user_agent = user_agent
    except Exception as error:
        logger.debug("Could not read the browser user agent: %s", error)


async def _stop_solver(solver: Any) -> None:
    """Stop a solver without letting teardown errors escape."""
    try:
        await solver.stop()
    except Exception as error:
        logger.debug("Error while closing the browser: %s", error)


def _finalize(
    result: CollectionResult,
    solver: Any,
    default_status: CollectionStatus,
    reason: Optional[str],
    elapsed: float,
) -> CollectionResult:
    """
    Fill in the final status and error reason of a failed attempt.

    If the solver recorded a proxy failure, the attempt is reported as a proxy
    error no matter which status the caller would have used otherwise.
    """
    proxy_reason = getattr(solver, "proxy_failure_reason", None)

    if proxy_reason is not None and result.cf_clearance is None:
        result.status = CollectionStatus.PROXY_ERROR
        result.error = f"proxy error: {proxy_reason}"
    else:
        result.status = default_status
        result.error = reason

    result.elapsed = elapsed

    return result


def _coerce_platform(
    platform: Any,
) -> Optional[ChallengePlatform]:
    """Normalise a detected challenge platform to :class:`ChallengePlatform`."""
    if platform is None or isinstance(platform, ChallengePlatform):
        return platform

    if isinstance(platform, str):
        for candidate in ChallengePlatform:
            if platform in (candidate.value, candidate.name, candidate.name.lower()):
                return candidate

        logger.debug("Ignoring unknown challenge platform %r", platform)
        return None

    logger.debug("Ignoring unexpected challenge platform %r", platform)
    return None


def _accepts_keyword(function: Callable[..., Any], name: str) -> bool:
    """Whether ``function`` accepts a keyword argument called ``name``."""
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return False

    return any(
        parameter.name == name
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


async def _solve_with_deadline(
    solver: Any, deadline: float, timeout: float
) -> bool:
    """
    Call ``solver.solve_challenge`` bounded by ``deadline``.

    Solvers written against the original signature (no timeout argument) are
    supported as well, so stubbed browsers keep working.
    """
    remaining = deadline - time.monotonic()

    if remaining <= 0:
        raise ChallengeTimeoutError(
            f"Cloudflare challenge was not solved within {timeout} seconds"
        )

    solve = solver.solve_challenge

    if _accepts_keyword(solve, "timeout"):
        outcome = solve(timeout=remaining)
    else:
        outcome = solve()

    if not inspect.isawaitable(outcome):
        return bool(outcome)

    try:
        return bool(await asyncio.wait_for(outcome, remaining))
    except ChallengeTimeoutError:
        raise
    except asyncio.TimeoutError as error:
        raise ChallengeTimeoutError(
            f"Cloudflare challenge was not solved within {timeout} seconds"
        ) from error


async def collect_clearance(
    url: str,
    *,
    timeout: float = 30.0,
    proxy: Optional[str] = None,
    user_agent: Optional[str] = None,
    http2: bool = True,
    http3: bool = True,
    headless: bool = True,
    solver_factory: Optional[Callable[..., Any]] = None,
) -> CollectionResult:
    """
    Collect a Cloudflare ``cf_clearance`` cookie for ``url``.

    This is the programmatic counterpart of the command line interface. It
    never raises for expected failures: browser launch problems, page request
    errors, proxy authentication failures and timeouts are all reported
    through the returned :class:`CollectionResult`, which also keeps the
    cookies and user agent that were obtained before the failure.

    Parameters
    ----------
    url : str
        The URL to collect the clearance cookie from.
    timeout : float
        Maximum number of seconds spent detecting and solving a challenge. The
        solving loop never clicks after this deadline.
    proxy : Optional[str]
        The proxy server URL to use, optionally including credentials.
    user_agent : Optional[str]
        The user agent to launch the browser with. When omitted, a recent
        random Chrome user agent is generated (falling back to the browser
        default if that is not possible).
    http2 : bool
        Enable or disable the usage of HTTP/2 for the browser requests.
    http3 : bool
        Enable or disable the usage of HTTP/3 for the browser requests.
    headless : bool
        Run the browser headless.
    solver_factory : Optional[Callable[..., CloudflareSolver]]
        Factory used to create the solver. Mainly useful for testing with a
        stubbed browser.

    Returns
    -------
    CollectionResult
        The structured result of the attempt.
    """
    started = time.monotonic()
    factory = solver_factory if solver_factory is not None else CloudflareSolver

    def elapsed() -> float:
        return max(0.0, time.monotonic() - started)

    configured_user_agent = user_agent

    if configured_user_agent is None:
        try:
            configured_user_agent = get_chrome_user_agent()
        except Exception as error:
            logger.warning("Could not determine a Chrome user agent: %s", error)

    result = CollectionResult(
        status=CollectionStatus.ERROR,
        url=url,
        user_agent=configured_user_agent,
        proxy=proxy,
    )

    def fail(
        default_status: CollectionStatus,
        reason: Optional[str],
        *,
        success_if_cookie: bool = False,
    ) -> CollectionResult:
        """
        Finish the attempt with a failure.

        A proxy authentication failure always wins over the status the caller
        would otherwise report. When ``success_if_cookie`` is set, a clearance
        cookie captured before the failure turns the attempt into a success.
        """
        if success_if_cookie and result.cf_clearance is not None:
            result.status = CollectionStatus.SUCCESS
            result.elapsed = elapsed()
            return result

        return _finalize(result, solver, default_status, reason, elapsed())

    try:
        solver = factory(
            user_agent=configured_user_agent,
            timeout=timeout,
            http2=http2,
            http3=http3,
            headless=headless,
            proxy=proxy,
        )
    except Exception as error:
        return _finalize(
            result,
            None,
            CollectionStatus.BROWSER_ERROR,
            f"failed to launch browser: {_describe_error(error)}",
            elapsed(),
        )

    try:
        try:
            await solver.start()
        except Exception as error:
            return _finalize(
                result,
                solver,
                CollectionStatus.BROWSER_ERROR,
                f"failed to launch browser: {_describe_error(error)}",
                elapsed(),
            )

        try:
            await solver.request_page(url)
        except Exception as error:
            await _capture_state(solver, result)

            if result.cf_clearance is not None:
                result.status = CollectionStatus.SUCCESS
                result.elapsed = elapsed()
                return result

            reason = _describe_error(error)

            if proxy is not None and _looks_like_proxy_error(reason):
                return _finalize(
                    result,
                    solver,
                    CollectionStatus.PROXY_ERROR,
                    f"proxy error while requesting the page: {reason}",
                    elapsed(),
                )

            return _finalize(
                result,
                solver,
                CollectionStatus.PAGE_ERROR,
                f"failed to request page: {reason}",
                elapsed(),
            )

        await _capture_state(solver, result)

        if result.cf_clearance is not None:
            result.status = CollectionStatus.SUCCESS
            result.elapsed = elapsed()
            return result

        # Keep the browser user agent metadata in sync with the real user
        # agent, so the cookies and the reported user agent stay same-origin.
        try:
            await solver.set_user_agent_metadata(await solver.get_user_agent())
        except Exception as error:
            logger.debug("Could not set the user agent metadata: %s", error)

        deadline = time.monotonic() + max(float(timeout), 0.0)

        try:
            detected = await asyncio.wait_for(
                solver.detect_challenge(), max(deadline - time.monotonic(), 0.0)
            )
            platform = _coerce_platform(detected)
        except (asyncio.TimeoutError, ChallengeTimeoutError):
            await _capture_state(solver, result)

            return fail(
                CollectionStatus.TIMEOUT,
                f"challenge detection timed out after {timeout} seconds",
                success_if_cookie=True,
            )
        except Exception as error:
            await _capture_state(solver, result)

            return fail(
                CollectionStatus.ERROR,
                f"failed to detect the challenge: {_describe_error(error)}",
                success_if_cookie=True,
            )

        result.challenge_type = platform

        if platform is None:
            await _capture_state(solver, result)

            if result.cf_clearance is not None:
                result.status = CollectionStatus.SUCCESS
                result.elapsed = elapsed()
                return result

            # A proxy that could not be authenticated never shows a challenge,
            # so it must not be reported as "no challenge".
            return fail(
                CollectionStatus.NO_CHALLENGE,
                None,
                success_if_cookie=True,
            )

        logger.info("Solving Cloudflare challenge [%s]...", platform.label)

        try:
            solved = await _solve_with_deadline(solver, deadline, timeout)
        except (asyncio.TimeoutError, ChallengeTimeoutError):
            solved = False
            timed_out = True
        except Exception as error:
            await _capture_state(solver, result)

            return fail(
                CollectionStatus.ERROR,
                f"failed while solving the challenge: {_describe_error(error)}",
                success_if_cookie=True,
            )
        else:
            timed_out = False

        await _capture_state(solver, result)

        if result.cf_clearance is not None:
            result.status = CollectionStatus.SUCCESS
            result.elapsed = elapsed()
            return result

        if timed_out:
            return fail(
                CollectionStatus.TIMEOUT,
                f"Cloudflare challenge was not solved within {timeout} seconds",
                success_if_cookie=True,
            )

        if solved:
            # The solver reported success but the cookie is no longer there.
            return fail(
                CollectionStatus.ERROR,
                "the clearance cookie disappeared before it could be read",
                success_if_cookie=True,
            )

        # The challenge went away without handing out a clearance cookie.
        return fail(CollectionStatus.NO_CHALLENGE, None, success_if_cookie=True)
    finally:
        await _stop_solver(solver)


async def gather_result(
    url: str,
    *,
    timeout: float = 30.0,
    proxy: Optional[str] = None,
    user_agent: Optional[str] = None,
    http2: bool = True,
    http3: bool = True,
    headless: bool = True,
    solver_factory: Optional[Callable[..., Any]] = None,
) -> CollectionResult:
    """Alias of :func:`collect_clearance` for callers that prefer this name."""
    return await collect_clearance(
        url,
        timeout=timeout,
        proxy=proxy,
        user_agent=user_agent,
        http2=http2,
        http3=http3,
        headless=headless,
        solver_factory=solver_factory,
    )


async def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="A simple program for scraping Cloudflare clearance (cf_clearance) cookies from websites issuing Cloudflare challenges to visitors"
    )

    parser.add_argument(
        "url",
        metavar="URL",
        help="The URL to scrape the Cloudflare clearance cookie from",
        type=str,
    )

    parser.add_argument(
        "-f",
        "--file",
        default=None,
        help="The file to write the Cloudflare clearance cookie information to, in JSON format",
        type=str,
    )

    parser.add_argument(
        "-t",
        "--timeout",
        default=30,
        help="The timeout in seconds to use for solving challenges",
        type=float,
    )

    parser.add_argument(
        "-p",
        "--proxy",
        default=None,
        help="The proxy server URL to use for the browser requests",
        type=str,
    )

    parser.add_argument(
        "-ua",
        "--user-agent",
        default=None,
        help="The user agent to use for the browser requests",
        type=str,
    )

    parser.add_argument(
        "--disable-http2",
        action="store_true",
        help="Disable the usage of HTTP/2 for the browser requests",
    )

    parser.add_argument(
        "--disable-http3",
        action="store_true",
        help="Disable the usage of HTTP/3 for the browser requests",
    )

    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run the browser in headed mode",
    )

    parser.add_argument(
        "-ac",
        "--all-cookies",
        action="store_true",
        help="Retrieve all cookies from the page, not just the Cloudflare clearance cookie",
    )

    parser.add_argument(
        "-c",
        "--curl",
        action="store_true",
        help="Get the cURL command for the request with the cookies and user agent",
    )

    parser.add_argument(
        "-w",
        "--wget",
        action="store_true",
        help="Get the Wget command for the request with the cookies and user agent",
    )

    parser.add_argument(
        "-a",
        "--aria2",
        action="store_true",
        help="Get the aria2 command for the request with the cookies and user agent",
    )

    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="Print the structured collection result as JSON",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        level=logging.INFO,
    )

    logging.getLogger("zendriver").setLevel(logging.WARNING)
    logging.info("Launching %s browser...", "headed" if args.headed else "headless")

    result = await collect_clearance(
        args.url,
        timeout=args.timeout,
        proxy=args.proxy,
        user_agent=args.user_agent,
        http2=not args.disable_http2,
        http3=not args.disable_http3,
        headless=not args.headed,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=4, ensure_ascii=False))

    logging.info("Status: %s", result.status.value)

    if result.challenge_type is not None:
        logging.info("Challenge type: %s", result.challenge_type.value)

    logging.info("Elapsed: %.2fs", result.elapsed)

    if result.status is CollectionStatus.NO_CHALLENGE:
        logging.info("No Cloudflare challenge detected.")
        return 0

    if result.cf_clearance is None:
        logging.error(
            "Failed to retrieve a Cloudflare clearance cookie: %s", result.error
        )
        return 1

    all_cookies = result.cookies
    clearance_cookie = result.cf_clearance
    user_agent = result.user_agent or ""
    proxy = result.proxy

    cookie_string = "; ".join(
        f'{cookie["name"]}={cookie["value"]}' for cookie in all_cookies
    )

    if args.all_cookies:
        logging.info("All cookies: %s", cookie_string)
    else:
        logging.info("Cookie: cf_clearance=%s", clearance_cookie["value"])

    logging.info("User agent: %s", user_agent)

    if args.curl:
        logging.info(
            COMMAND.format(
                name="cURL",
                binary="curl",
                cookies=(
                    cookie_string
                    if args.all_cookies
                    else f'cf_clearance={clearance_cookie["value"]}'
                ),
                user_agent=user_agent,
                url=(f"--proxy {proxy} {args.url}" if proxy is not None else args.url),
            )
        )

    if args.wget:
        if proxy is not None:
            logging.warning(
                "Proxies must be set in an environment variable or config file for Wget."
            )

        logging.info(
            COMMAND.format(
                name="Wget",
                binary="wget",
                cookies=(
                    cookie_string
                    if args.all_cookies
                    else f'cf_clearance={clearance_cookie["value"]}'
                ),
                user_agent=user_agent,
                url=args.url,
            )
        )

    if args.aria2:
        if proxy is not None and proxy.casefold().startswith("socks"):
            logging.warning("SOCKS proxies are not supported by aria2.")

        logging.info(
            COMMAND.format(
                name="aria2",
                binary="aria2c",
                cookies=(
                    cookie_string
                    if args.all_cookies
                    else f'cf_clearance={clearance_cookie["value"]}'
                ),
                user_agent=user_agent,
                url=(
                    f"--all-proxy {proxy} {args.url}"
                    if proxy is not None
                    else args.url
                ),
            )
        )

    if args.file is None:
        return 0

    logging.info("Writing Cloudflare clearance cookie information to %s...", args.file)

    try:
        with open(args.file, encoding="utf-8") as file:
            json_data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        json_data: Dict[str, List[Dict[str, Any]]] = {}

    local_timezone = datetime.now(timezone.utc).astimezone().tzinfo
    expires = clearance_cookie.get("expires")

    if expires is None or expires < 0:
        expires = datetime.now(timezone.utc).timestamp()

    unix_timestamp = expires - timedelta(days=365).total_seconds()
    timestamp = datetime.fromtimestamp(unix_timestamp, tz=local_timezone).isoformat()

    json_data.setdefault(clearance_cookie["domain"], []).append(
        {
            "unix_timestamp": int(unix_timestamp),
            "timestamp": timestamp,
            "cf_clearance": clearance_cookie["value"],
            "cookies": all_cookies,
            "user_agent": user_agent,
            "proxy": proxy,
        }
    )

    with open(args.file, "w", encoding="utf-8") as file:
        json.dump(json_data, file, indent=4)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
