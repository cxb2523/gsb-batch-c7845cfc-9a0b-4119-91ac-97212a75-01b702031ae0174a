"""Playwright based scraper with per-page fault tolerance.

A batch scrape must survive individual page failures: an unreachable host, a
navigation timeout or a non-2xx response only disqualifies that single page.
The caller receives the successfully scraped documents (in the original URL
order) together with a :class:`PageReport` that lists every page's outcome.
"""

from typing import Optional

from playwright.async_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)
from rich.progress import Progress
from rich.progress import SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn

from airbyte_connector_generator_poc.logger import logger
from airbyte_connector_generator_poc.report import PageReport

# Milliseconds Playwright waits for a navigation to settle.
DEFAULT_TIMEOUT_MS = 30_000
# A page that keeps polling (analytics, websockets, SSE) never reaches
# "networkidle". Cap that extra wait so one chatty page cannot stall the batch.
DEFAULT_IDLE_TIMEOUT_MS = 5_000

progress = Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    TimeElapsedColumn(),
)


class PageScrapeError(Exception):
    """A single page failed for a well-understood reason (e.g. non-2xx)."""


class ScrapeResult(dict):
    """``{url: html}`` for the pages that scraped successfully.

    Also carries the per-page :class:`PageReport`, so callers can tell which
    URLs were dropped and why. Behaves like the plain dict the scraper used to
    return, keeping existing call sites working.
    """

    def __init__(self, content_by_url: Optional[dict[str, str]] = None,
                 report: Optional[PageReport] = None):
        super().__init__(content_by_url or {})
        self.report = report if report is not None else PageReport()

    @property
    def failures(self) -> list:
        return self.report.failed_pages


def describe_scrape_error(error: BaseException) -> str:
    """Turn a Playwright exception into a short, single line user facing reason.

    Playwright errors end with a verbose ``Call log: ...`` block; drop it so the
    console table and ``.skyffel/scrape_report.json`` stay readable.
    """
    message = str(error).split("Call log:")[0]
    return " ".join(message.split()) or error.__class__.__name__


async def scrape_url(url: str, browser, timeout_ms: int = DEFAULT_TIMEOUT_MS,
                     idle_timeout_ms: int = DEFAULT_IDLE_TIMEOUT_MS) -> str:
    """Scrape a single URL, raising on navigation errors or non-2xx responses.

    ``timeout_ms`` bounds the navigation itself; ``idle_timeout_ms`` bounds the
    optional wait for the network to go quiet after the document loaded.
    """
    page = await browser.new_page()
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        if response is not None and not response.ok:
            raise PageScrapeError(
                f"HTTP {response.status} {response.status_text}".strip())

        try:
            await page.wait_for_load_state("networkidle", timeout=idle_timeout_ms)
        except PlaywrightTimeoutError:
            # The document itself loaded; a chatty page (analytics, websockets)
            # never reaches networkidle. The HTML we already have is usable.
            logger.debug(
                "networkidle not reached for %s, using current content", url)

        return await page.content()
    finally:
        await page.close()


async def scrape_urls_with_browser(
    urls: list[str],
    browser,
    report: Optional[PageReport] = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    idle_timeout_ms: int = DEFAULT_IDLE_TIMEOUT_MS,
    progress_task=None,
    progress_bar: Optional[Progress] = None,
) -> ScrapeResult:
    """Scrape every URL with an already launched browser.

    Individual page failures are recorded in the report and never abort the
    batch. Successfully scraped documents keep the order of ``urls``.
    """
    if report is None:
        report = PageReport(urls)

    content_by_url: dict[str, str] = {}

    for url in urls:
        if progress_bar is not None and progress_task is not None:
            progress_bar.update(
                progress_task,
                description=f"[bold cyan]Scraping[/bold cyan] {url}")
        try:
            content = await scrape_url(url, browser, timeout_ms=timeout_ms,
                                       idle_timeout_ms=idle_timeout_ms)
        except PageScrapeError as error:
            reason = str(error)
            logger.warning("Failed to scrape %s: %s", url, reason)
            report.record_scrape_failure(url, reason)
        except PlaywrightTimeoutError as error:
            reason = f"timeout after {timeout_ms}ms: {describe_scrape_error(error)}"
            logger.warning("Failed to scrape %s: %s", url, reason)
            report.record_scrape_failure(url, reason)
        except PlaywrightError as error:
            reason = describe_scrape_error(error)
            logger.warning("Failed to scrape %s: %s", url, reason)
            report.record_scrape_failure(url, reason)
        except Exception as error:  # noqa: BLE001 - never kill the batch
            reason = f"{error.__class__.__name__}: {describe_scrape_error(error)}"
            logger.warning("Failed to scrape %s: %s", url, reason)
            report.record_scrape_failure(url, reason)
        else:
            content_by_url[url] = content
            report.record_scrape_success(url)
        finally:
            if progress_bar is not None and progress_task is not None:
                progress_bar.update(progress_task, advance=1)

    return ScrapeResult(content_by_url, report)


async def scrape_urls(
    urls: list[str],
    report: Optional[PageReport] = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    idle_timeout_ms: int = DEFAULT_IDLE_TIMEOUT_MS,
) -> ScrapeResult:
    """Scrape every URL, tolerating individual failures.

    Returns a :class:`ScrapeResult` holding the successfully scraped
    ``{url: html}`` mapping in the original URL order; its ``report`` attribute
    describes every page's outcome.
    """
    if report is None:
        report = PageReport(urls)

    if not urls:
        return ScrapeResult({}, report)

    with progress:
        scrape_progress_task = progress.add_task(
            "[bold cyan]Scraping URLs...", total=len(urls))

        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch()
            except Exception as error:  # noqa: BLE001 - no browser, no pages
                reason = (
                    "could not launch Chromium: "
                    + describe_scrape_error(error)
                )
                logger.error(reason)
                for url in urls:
                    report.record_scrape_failure(url, reason)
                return ScrapeResult({}, report)

            try:
                result = await scrape_urls_with_browser(
                    urls, browser, report=report, timeout_ms=timeout_ms,
                    idle_timeout_ms=idle_timeout_ms,
                    progress_task=scrape_progress_task, progress_bar=progress)
            finally:
                await browser.close()

        progress.update(scrape_progress_task,
                        description="[bold green]✅ Scraped URLs")

    logger.debug("Scraped %d/%d URL(s)", len(result), len(urls))
    return result
