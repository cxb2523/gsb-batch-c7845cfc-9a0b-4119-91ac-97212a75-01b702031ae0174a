"""Diffbot Crawler API: start crawls, manage crawler jobs."""

import asyncio
import csv
import io
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, Iterator, List, Optional

from .errors import APIError, CrawlMonitorError, ValidationError

if TYPE_CHECKING:
    from .client import Diffbot, DiffbotAsync

#: Default number of retries for a failing status / URL-list poll in watch mode.
DEFAULT_CRAWL_MAX_RETRIES = 3
#: Default delay (seconds) between poll retries in watch mode.
DEFAULT_CRAWL_RETRY_INTERVAL = 1.0


class CrawlEventType(Enum):
    JOB_CREATED = "job_created"
    URL_PROCESSED = "url_processed"
    #: Emitted once when watch-mode polling gives up after retries are exhausted,
    #: immediately before CrawlMonitorError is raised.
    MONITOR_INTERRUPTED = "monitor_interrupted"


@dataclass
class CrawlEvent:
    """An event yielded while a crawl is in flight."""
    event_type: CrawlEventType
    timestamp: str
    details: Dict[str, Any]


@dataclass
class _PollOutcome:
    """Result of one successful poll iteration."""
    jobs_present: bool
    status_code: int = 0
    message: str = ""
    events: List[CrawlEvent] = field(default_factory=list)


def crawl(
    client: "Diffbot",
    site: str,
    hops: int = 2,
    job_name: Optional[str] = None,
    max_to_crawl: int = 100,
    max_to_process: int = 100,
    restrict_domain: bool = True,
    api_url: str = "",
    crawl_delay: float = -1,
    url_crawl_pattern: Optional[str] = None,
    url_process_pattern: Optional[str] = None,
    obey_robots: bool = False,
    use_proxies: bool = False,
    custom_headers: Optional[str] = None,
    watch: bool = False,
    poll_interval: float = 2.0,
    max_retries: int = DEFAULT_CRAWL_MAX_RETRIES,
    retry_interval: float = DEFAULT_CRAWL_RETRY_INTERVAL,
) -> Iterator[CrawlEvent]:
    """Create a crawler job and yield a JOB_CREATED event. If watch=True, poll until
    completion and yield URL_PROCESSED events for each crawled URL.

    While watching, a failed status query or URL-list download does not abort the
    generator immediately: the poll is retried up to ``max_retries`` times, waiting
    ``retry_interval`` seconds between attempts. If every attempt fails, a
    MONITOR_INTERRUPTED event is yielded (so the interruption shows up in the event
    stream) and :class:`CrawlMonitorError` is raised; events yielded before the
    interruption are unaffected. A job that the API reports as failed still raises
    :class:`APIError`.
    """
    _validate_watch_options(max_retries, retry_interval)

    if not site.startswith("http"):
        site = f"https://{site}"
    if not job_name:
        job_name = f"crawl-{int(time.time())}"

    params = _build_crawl_params(
        client, job_name, site, max_to_crawl, max_to_process, hops, restrict_domain,
        api_url, crawl_delay, url_crawl_pattern, url_process_pattern,
        obey_robots, use_proxies, custom_headers,
    )
    response = client._http.get(client.crawler_url, params=params)
    client._raise_for_status(response)

    yield CrawlEvent(
        event_type=CrawlEventType.JOB_CREATED,
        timestamp=datetime.now().isoformat(),
        details={"job_name": job_name},
    )

    if not watch:
        return

    seen_urls: set = set()

    while True:
        try:
            outcome = _poll_with_retries(
                client, job_name, seen_urls, max_retries, retry_interval,
            )
        except CrawlMonitorError:
            yield _monitor_interrupted_event(job_name)
            raise

        if not outcome.jobs_present:
            break

        for event in outcome.events:
            yield event

        if outcome.status_code not in (0, 7):
            if "fail" in outcome.message.lower() or "error" in outcome.message.lower():
                raise APIError(500, f"Crawler job failed: {outcome.message}")
            break

        time.sleep(poll_interval)


def _validate_watch_options(max_retries: int, retry_interval: float) -> None:
    if max_retries < 0:
        raise ValidationError("max_retries must be >= 0")
    if retry_interval < 0:
        raise ValidationError("retry_interval must be >= 0")


def _monitor_interrupted_event(job_name: str) -> CrawlEvent:
    return CrawlEvent(
        event_type=CrawlEventType.MONITOR_INTERRUPTED,
        timestamp=datetime.now().isoformat(),
        details={"job_name": job_name, "reason": "poll retries exhausted"},
    )


def _poll_once(
    client: "Diffbot", job_name: str, seen_urls: set,
) -> _PollOutcome:
    """Perform one status + URL-list poll. Raises on any transport/API failure."""
    status_response = client._http.get(
        client.crawler_url,
        params={"token": client.token, "name": job_name},
    )
    client._raise_for_status(status_response)
    jobs = status_response.json().get("jobs", [])
    if not jobs:
        return _PollOutcome(jobs_present=False)

    job_status = jobs[0].get("jobStatus", {})
    status_code = job_status.get("status", 0)
    message = job_status.get("message", "")

    urls_response = client._http.get(
        f"{client.crawler_url}/data",
        params={"token": client.token, "name": job_name, "type": "urls"},
        follow_redirects=True,
    )
    client._raise_for_status(urls_response)
    events: List[CrawlEvent] = []
    if urls_response.content:
        events = _parse_url_csv(urls_response.text, seen_urls)

    return _PollOutcome(
        jobs_present=True,
        status_code=status_code,
        message=message,
        events=events,
    )


def _poll_with_retries(
    client: "Diffbot",
    job_name: str,
    seen_urls: set,
    max_retries: int,
    retry_interval: float,
) -> _PollOutcome:
    """Poll once, retrying transient failures up to ``max_retries`` times."""
    last_error: Optional[BaseException] = None
    # The initial attempt plus max_retries retries.
    for attempt in range(max_retries + 1):
        try:
            return _poll_once(client, job_name, seen_urls)
        except Exception as exc:  # noqa: BLE001 - any request failure is retryable
            last_error = exc
            if attempt < max_retries:
                time.sleep(retry_interval)
    raise CrawlMonitorError(job_name, max_retries + 1, last_error) from last_error  # type: ignore[arg-type]


def _build_crawl_params(client: Any, job_name: str, site: str, max_to_crawl: int,
                        max_to_process: int, hops: int, restrict_domain: bool,
                        api_url: str, crawl_delay: float, url_crawl_pattern: Optional[str],
                        url_process_pattern: Optional[str], obey_robots: bool,
                        use_proxies: bool, custom_headers: Optional[str]) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "token": client.token,
        "name": job_name,
        "seeds": site,
        "maxToCrawl": max_to_crawl,
        "maxToProcess": max_to_process,
        "maxHops": hops,
        "restrictDomain": 1 if restrict_domain else 0,
    }
    if api_url:
        params["apiUrl"] = api_url
    if crawl_delay > 0:
        params["crawlDelay"] = crawl_delay
    if url_crawl_pattern:
        params["urlCrawlPattern"] = url_crawl_pattern
    if url_process_pattern:
        params["urlProcessPattern"] = url_process_pattern
    if obey_robots:
        params["obeyRobots"] = 1
    if use_proxies:
        params["useProxies"] = 1
    if custom_headers:
        params["customHeaders"] = custom_headers
    return params


def _parse_url_csv(text: str, seen_urls: set) -> List[CrawlEvent]:
    events = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        url = row.get("Url", "").strip('"')
        if url and url not in seen_urls:
            seen_urls.add(url)
            events.append(CrawlEvent(
                event_type=CrawlEventType.URL_PROCESSED,
                timestamp=row.get("Crawled Time", datetime.now().isoformat()),
                details={"url": url, "status": row.get("Crawl Status", "unknown")},
            ))
    return events


async def crawl_async(
    client: "DiffbotAsync",
    site: str,
    hops: int = 2,
    job_name: Optional[str] = None,
    max_to_crawl: int = 100,
    max_to_process: int = 100,
    restrict_domain: bool = True,
    api_url: str = "",
    crawl_delay: float = -1,
    url_crawl_pattern: Optional[str] = None,
    url_process_pattern: Optional[str] = None,
    obey_robots: bool = False,
    use_proxies: bool = False,
    custom_headers: Optional[str] = None,
    watch: bool = False,
    poll_interval: float = 2.0,
    max_retries: int = DEFAULT_CRAWL_MAX_RETRIES,
    retry_interval: float = DEFAULT_CRAWL_RETRY_INTERVAL,
) -> AsyncIterator[CrawlEvent]:
    """Async counterpart of :func:`crawl` with the same watch-mode retry semantics."""
    _validate_watch_options(max_retries, retry_interval)

    if not site.startswith("http"):
        site = f"https://{site}"
    if not job_name:
        job_name = f"crawl-{int(time.time())}"

    params = _build_crawl_params(
        client, job_name, site, max_to_crawl, max_to_process, hops, restrict_domain,
        api_url, crawl_delay, url_crawl_pattern, url_process_pattern,
        obey_robots, use_proxies, custom_headers,
    )
    response = await client._http.get(client.crawler_url, params=params)
    client._raise_for_status(response)

    yield CrawlEvent(
        event_type=CrawlEventType.JOB_CREATED,
        timestamp=datetime.now().isoformat(),
        details={"job_name": job_name},
    )

    if not watch:
        return

    seen_urls: set = set()

    while True:
        try:
            outcome = await _poll_with_retries_async(
                client, job_name, seen_urls, max_retries, retry_interval,
            )
        except CrawlMonitorError:
            yield _monitor_interrupted_event(job_name)
            raise

        if not outcome.jobs_present:
            break

        for event in outcome.events:
            yield event

        if outcome.status_code not in (0, 7):
            if "fail" in outcome.message.lower() or "error" in outcome.message.lower():
                raise APIError(500, f"Crawler job failed: {outcome.message}")
            break

        await asyncio.sleep(poll_interval)


async def _poll_once_async(
    client: "DiffbotAsync", job_name: str, seen_urls: set,
) -> _PollOutcome:
    """Async counterpart of :func:`_poll_once`."""
    status_response = await client._http.get(
        client.crawler_url,
        params={"token": client.token, "name": job_name},
    )
    client._raise_for_status(status_response)
    jobs = status_response.json().get("jobs", [])
    if not jobs:
        return _PollOutcome(jobs_present=False)

    job_status = jobs[0].get("jobStatus", {})
    status_code = job_status.get("status", 0)
    message = job_status.get("message", "")

    urls_response = await client._http.get(
        f"{client.crawler_url}/data",
        params={"token": client.token, "name": job_name, "type": "urls"},
        follow_redirects=True,
    )
    client._raise_for_status(urls_response)
    events: List[CrawlEvent] = []
    if urls_response.content:
        events = _parse_url_csv(urls_response.text, seen_urls)

    return _PollOutcome(
        jobs_present=True,
        status_code=status_code,
        message=message,
        events=events,
    )


async def _poll_with_retries_async(
    client: "DiffbotAsync",
    job_name: str,
    seen_urls: set,
    max_retries: int,
    retry_interval: float,
) -> _PollOutcome:
    """Async counterpart of :func:`_poll_with_retries`."""
    last_error: Optional[BaseException] = None
    # The initial attempt plus max_retries retries.
    for attempt in range(max_retries + 1):
        try:
            return await _poll_once_async(client, job_name, seen_urls)
        except Exception as exc:  # noqa: BLE001 - any request failure is retryable
            last_error = exc
            if attempt < max_retries:
                await asyncio.sleep(retry_interval)
    raise CrawlMonitorError(job_name, max_retries + 1, last_error) from last_error  # type: ignore[arg-type]


async def crawl_list_jobs_async(client: "DiffbotAsync") -> List[Dict[str, Any]]:
    response = await client._http.get(client.crawler_url, params={"token": client.token})
    client._raise_for_status(response)
    return response.json().get("jobs", [])


async def crawl_get_job_async(client: "DiffbotAsync", job_name: str) -> Dict[str, Any]:
    response = await client._http.get(
        client.crawler_url,
        params={"token": client.token, "name": job_name},
    )
    client._raise_for_status(response)
    jobs = response.json().get("jobs", [])
    return jobs[0] if jobs else {}


async def crawl_delete_job_async(client: "DiffbotAsync", job_name: str) -> None:
    response = await client._http.get(
        client.crawler_url,
        params={"token": client.token, "name": job_name, "delete": 1},
    )
    client._raise_for_status(response)


def crawl_list_jobs(client: "Diffbot") -> List[Dict[str, Any]]:
    response = client._http.get(client.crawler_url, params={"token": client.token})
    client._raise_for_status(response)
    return response.json().get("jobs", [])


def crawl_get_job(client: "Diffbot", job_name: str) -> Dict[str, Any]:
    response = client._http.get(
        client.crawler_url,
        params={"token": client.token, "name": job_name},
    )
    client._raise_for_status(response)
    jobs = response.json().get("jobs", [])
    return jobs[0] if jobs else {}


def crawl_delete_job(client: "Diffbot", job_name: str) -> None:
    response = client._http.get(
        client.crawler_url,
        params={"token": client.token, "name": job_name, "delete": 1},
    )
    client._raise_for_status(response)
