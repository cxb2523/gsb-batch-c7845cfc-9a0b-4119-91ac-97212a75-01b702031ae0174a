import asyncio
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urljoin

from spark_history_mcp.config.config import ServerConfig

# HTML client for Spark History Server web interface

logger = logging.getLogger(__name__)

# Extra wait after the page reaches network idle, so client-side rendering
# (JavaScript) has time to finish before the content is captured.
RENDER_WAIT_MS = 3000

# HTTP statuses that indicate the authenticated session is no longer valid.
AUTH_FAILURE_STATUSES = (401, 403)

VIEWPORT_HTML = {"width": 1280, "height": 800}
VIEWPORT_SCREENSHOT = {"width": 2560, "height": 800}


class AuthenticationError(Exception):
    """Raised when the Spark History Server rejects a page load with 401/403."""

    def __init__(self, status: int, url: str):
        self.status = status
        self.url = url
        super().__init__(f"authentication failed (HTTP {status}) for {url}")


@dataclass
class PageResult:
    """Settled outcome of collecting a single page in a batch.

    Exactly one of ``content`` / ``error`` is set: ``content`` holds the
    rendered HTML (or the screenshot file path) on success, ``error`` holds
    the human-readable failure reason otherwise.
    """

    path: str
    success: bool
    content: Optional[str] = None
    error: Optional[str] = None
    # True when the page only succeeded after (or failed despite) one
    # re-authentication retry.
    retried: bool = False


class SparkHtmlClient:
    """Headless-browser client for the rendered Spark History Server UI.

    The browser is launched lazily and reused across pages of a batch; it is
    closed when the batch finishes (``collect_pages``) or when ``close()`` is
    called explicitly. Pass ``browser_factory`` (an async callable returning a
    browser-like object) to substitute a test double instead of a real
    Playwright browser.
    """

    def __init__(
        self,
        server_config: ServerConfig,
        browser_factory: Optional[Callable[[], Awaitable[object]]] = None,
    ):
        self.config = server_config
        self.base_url = self.config.url.rstrip("/") + "/history/"
        self.auth: Optional[Tuple[str, str]] = None
        self.browser = None
        self._context = None
        self._playwright = None
        self._browser_factory = browser_factory
        # Per-page timeout in seconds, reused from the server config.
        self.page_timeout = self.config.timeout or 30

        # Set up authentication if provided
        if self.config.auth:
            if self.config.auth.username and self.config.auth.password:
                self.auth = (self.config.auth.username, self.config.auth.password)

    # ------------------------------------------------------------------
    # Browser / session lifecycle
    # ------------------------------------------------------------------

    async def _ensure_browser(self):
        if self.browser is None:
            if self._browser_factory is not None:
                self.browser = await self._browser_factory()
            else:
                # Imported lazily so the module (and its tests) work without
                # Playwright installed.
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self.browser = await self._playwright.chromium.launch()
        return self.browser

    async def _ensure_context(self):
        """Return the current authenticated browser context, creating it once."""
        if self._context is None:
            browser = await self._ensure_browser()
            kwargs = {}
            if self.auth:
                kwargs["http_credentials"] = {
                    "username": self.auth[0],
                    "password": self.auth[1],
                }
            self._context = await browser.new_context(**kwargs)
        return self._context

    async def _renew_auth_session(self):
        """Drop the current browser context and establish a fresh authenticated one."""
        logger.info("Re-establishing authenticated browser session")
        if self._context is not None:
            try:
                await self._context.close()
            finally:
                self._context = None
        await self._ensure_context()

    async def close(self):
        """Close the context, browser and Playwright driver, if any are open."""
        if self._context is not None:
            try:
                await self._context.close()
            finally:
                self._context = None
        if self.browser is not None:
            try:
                await self.browser.close()
            finally:
                self.browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    # ------------------------------------------------------------------
    # Single-page rendering
    # ------------------------------------------------------------------

    def _page_url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    async def _render_page(
        self, path: str, kind: str = "html", save_path: Optional[str] = None
    ) -> str:
        """Render one page in the current session and return its content.

        Returns the rendered HTML, or the screenshot file path when
        ``kind == "screenshot"``. Raises :class:`AuthenticationError` when the
        server answers 401/403.
        """
        context = await self._ensure_context()
        page = await context.new_page()
        try:
            await page.set_viewport_size(
                VIEWPORT_SCREENSHOT if kind == "screenshot" else VIEWPORT_HTML
            )
            url = self._page_url(path)
            response = await page.goto(url)
            status = getattr(response, "status", None)
            if status in AUTH_FAILURE_STATUSES:
                raise AuthenticationError(status, url)

            # Wait for network to be idle (no more than 2 connections for at
            # least 500 ms), then give client-side rendering a moment.
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(RENDER_WAIT_MS)

            if kind == "screenshot":
                filename = save_path or os.path.join(
                    tempfile.gettempdir(), f"{uuid.uuid4()}.jpg"
                )
                os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
                await page.screenshot(
                    path=filename, type="jpeg", quality=100, full_page=True
                )
                return filename

            return await page.content()
        finally:
            await page.close()

    async def _render_with_auth_retry(
        self,
        path: str,
        kind: str = "html",
        save_path: Optional[str] = None,
        on_retry: Optional[Callable[[], None]] = None,
    ) -> str:
        """Render a page, re-authenticating and retrying once on 401/403.

        The retry re-establishes the authenticated session exactly once; a
        second 401/403 propagates. ``on_retry`` (if given) is invoked when the
        retry is triggered, so callers can record that it happened even when
        the retried attempt also fails.
        """
        try:
            return await self._render_page(path, kind=kind, save_path=save_path)
        except AuthenticationError:
            if not self.auth:
                raise
            if on_retry is not None:
                on_retry()
            await self._renew_auth_session()
            return await self._render_page(path, kind=kind, save_path=save_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def collect_pages(
        self,
        paths: List[str],
        kind: str = "html",
        save_dir: Optional[str] = None,
    ) -> List[PageResult]:
        """Collect a batch of pages, settling each one independently.

        Pages are processed sequentially on a single reused browser instance;
        the browser is closed when the batch finishes. Each page gets its own
        timeout (``ServerConfig.timeout``) and, when credentials are
        configured, one re-authentication retry on 401/403. A failed page is
        recorded with its failure reason and never aborts the remaining pages.

        Returns one :class:`PageResult` per input path, in input order.
        """
        results: List[PageResult] = []
        try:
            for path in paths:
                save_path = None
                if kind == "screenshot":
                    directory = save_dir or tempfile.gettempdir()
                    save_path = os.path.join(directory, f"{uuid.uuid4()}.jpg")
                results.append(await self._collect_one(path, kind, save_path))
        finally:
            await self.close()
        return results

    async def _collect_one(
        self, path: str, kind: str, save_path: Optional[str]
    ) -> PageResult:
        retried = False

        def mark_retried():
            nonlocal retried
            retried = True

        try:

            async def attempt():
                return await self._render_with_auth_retry(
                    path, kind=kind, save_path=save_path, on_retry=mark_retried
                )

            content = await asyncio.wait_for(attempt(), timeout=self.page_timeout)
            return PageResult(path=path, success=True, content=content, retried=retried)
        except AuthenticationError as exc:
            logger.warning("Page %s failed: %s", path, exc)
            return PageResult(path=path, success=False, error=str(exc), retried=retried)
        except asyncio.TimeoutError:
            message = f"timed out after {self.page_timeout} seconds"
            logger.warning("Page %s failed: %s", path, message)
            return PageResult(path=path, success=False, error=message, retried=retried)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            logger.warning("Page %s failed: %s", path, message)
            return PageResult(path=path, success=False, error=message, retried=retried)

    async def get_rendered_html(self, path):
        """
        Fetches and returns the fully rendered HTML content of a Spark UI page.

        This method uses Playwright to launch a headless browser, navigate to the specified path
        on the Spark UI, wait for the page to fully render (including any JavaScript execution),
        and then return the complete HTML content.

        Args:
            path: The path to navigate to, relative to the base URL

        Returns:
            str: The fully rendered HTML content of the page
        """
        return await self._render_with_auth_retry(path, kind="html")

    async def get_screenshot(self, path, save_path=None):
        """
        Takes a screenshot of the page at the given path and saves it.

        Args:
            path: The path to navigate to for the screenshot
            save_path: The file path where the screenshot should be saved.
                       If None, saves to a random filename in /tmp.

        Returns:
            The full path to the saved screenshot
        """
        return await self._render_with_auth_retry(
            path, kind="screenshot", save_path=save_path
        )


async def main():
    async with SparkHtmlClient(ServerConfig(url="http://localhost:18080")) as client:
        # Example with custom save path
        await client.get_screenshot(
            "/spark-e975c1c221934381b99772c54ae4b8e6/jobs/", "spark_executors.jpg"
        )
        # html = await client.get_rendered_html("spark-e975c1c221934381b99772c54ae4b8e6/executors/")

    # Save the HTML to a file
    # with open("rendered_page.html", "w", encoding="utf-8") as f:
    #     f.write(html)
    #
    # print("HTML content saved to rendered_page.html")


if __name__ == "__main__":
    asyncio.run(main())
