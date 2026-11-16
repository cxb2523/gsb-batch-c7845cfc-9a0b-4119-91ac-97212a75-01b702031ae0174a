"""Layer 1: ATS Mass Poller -- High-speed 10,000+ slug JSON API polling.

Harvests company slugs from GitHub README.md repositories, then runs an
async loop that pings Greenhouse, Lever, and Ashby JSON endpoints every
4 hours. Only returns jobs posted within the last 48 hours whose titles
contain intern/grad/junior keywords.

Costs zero tokens, takes milliseconds per slug, and gives exact Unix
timestamps of when each job was posted.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from src.http_client import get_client
from src.logging import get_logger
from src.radar.core.models import JobObservation

logger = get_logger("ats_mass_poller")

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_API = "https://api.lever.co/v0/postings/{slug}"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

# Per-request ceiling for a single slug poll. A handful of boards (notably
# Lever) can take 15-35s to respond; without a hard cap one slow slug stalls
# the entire platform's asyncio.gather and the sweep hangs.
MASS_POLL_REQUEST_TIMEOUT = 20.0

# Per-board poll outcomes. Every slug polled in a round ends in exactly one
# of these, so operators can tell "board has no fresh junior jobs" apart from
# "board could not be fetched this round".
OUTCOME_MATCHED = "matched"  # 200 + valid payload + >=1 job passed the filters
OUTCOME_NO_MATCH = "no_match"  # 200 + valid payload, nothing passed the filters
OUTCOME_TIMEOUT = "timeout"  # exceeded MASS_POLL_REQUEST_TIMEOUT
OUTCOME_HTTP_ERROR = "http_error"  # non-200 status
OUTCOME_INVALID_RESPONSE = "invalid_response"  # 200 but malformed JSON/structure
OUTCOME_REQUEST_ERROR = "request_error"  # connection/DNS/TLS etc. failure

_FAILURE_OUTCOMES = frozenset(
    {OUTCOME_TIMEOUT, OUTCOME_HTTP_ERROR, OUTCOME_INVALID_RESPONSE, OUTCOME_REQUEST_ERROR}
)


@dataclass
class BoardPollResult:
    """Auditable outcome of polling a single ATS board (platform + slug)."""

    platform: str
    slug: str
    outcome: str
    reason: str = ""
    jobs_found: int = 0
    duration_s: float = 0.0

    @property
    def failed(self) -> bool:
        return self.outcome in _FAILURE_OUTCOMES


@dataclass
class MassPollSummary:
    """Round-level rollup returned alongside the jobs from poll_all_mass_slugs."""

    total_boards: int = 0
    jobs_found: int = 0
    elapsed_s: float = 0.0
    outcomes: dict[str, int] = field(default_factory=dict)
    board_results: list[BoardPollResult] = field(default_factory=list)
    platform_errors: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[BoardPollResult]:
        """Boards that could not be fetched (distinct from legit no_match)."""
        return [r for r in self.board_results if r.failed]

    @classmethod
    def build(
        cls,
        board_results: list[BoardPollResult],
        jobs_found: int,
        elapsed_s: float,
        platform_errors: list[str] | None = None,
    ) -> MassPollSummary:
        outcomes: dict[str, int] = {}
        for r in board_results:
            outcomes[r.outcome] = outcomes.get(r.outcome, 0) + 1
        return cls(
            total_boards=len(board_results),
            jobs_found=jobs_found,
            elapsed_s=elapsed_s,
            outcomes=outcomes,
            board_results=board_results,
            platform_errors=list(platform_errors or []),
        )


async def _timed_request(client: Any, method: str, url: str, **kwargs: Any) -> Any:
    """Issue one ATS request under a hard wall-clock cap.

    The httpx client timeout covers socket ops but the whole request (DNS +
    connect + read + json) can still exceed it if the server dribbles bytes.
    Wrapping in asyncio.timeout bounds the total so a slow board can never
    block the sweep.

    Raises TimeoutError when the 20s cap is hit and propagates any other
    request exception so callers can classify the failure per board.
    """
    async with asyncio.timeout(MASS_POLL_REQUEST_TIMEOUT):
        return await getattr(client, method)(url, **kwargs)


_COMMUNITY_README_URLS = [
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README.md",
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
    "https://raw.githubusercontent.com/LorenzoLaCorte/european-tech-internships-2026/main/README.md",
    "https://raw.githubusercontent.com/DereC4/internships-and-newgrad/main/README.md",
    "https://raw.githubusercontent.com/pittcsc/Summer2026-Internships/dev/README.md",
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/main/README.md",
    "https://raw.githubusercontent.com/cvrve/Summer2025-Internships/main/README.md",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/dev/README.md",
    "https://raw.githubusercontent.com/Coding-Crashkurse/Summer-2026-Internships/main/README.md",
    "https://raw.githubusercontent.com/AlanChen4/Summer-2026-SWE-Internships/main/README.md",
]

_TITLE_KEYWORDS = re.compile(
    r"intern|internship|new grad|graduate|university|early career|"
    r"entry.level|junior|associate|campus|co.op|coop",
    re.IGNORECASE,
)

_ATS_SLUG_RE = re.compile(
    r"https?://(?:boards\.greenhouse\.io|jobs\.lever\.co|jobs\.ashbyhq\.com|"
    r"apply\.workable\.com)/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

_SLUG_STORE: dict[str, set[str]] = {"greenhouse": set(), "lever": set(), "ashby": set()}
_SLUG_LAST_HARVEST = 0.0
_SLUG_COUNT = 0


async def harvest_slugs_from_github_readmes() -> int:
    """Pull all community GitHub README.md files and regex-extract company slugs.

    Scrapes every ATS URL from the raw markdown, extracts the {company_slug},
    and deduplicates them into the global _SLUG_STORE.

    Returns the total number of unique slugs harvested across all platforms.
    """
    global _SLUG_STORE, _SLUG_LAST_HARVEST, _SLUG_COUNT

    client = await get_client("ats_mass_poller", timeout=15.0)
    new_slugs = 0
    for url in _COMMUNITY_README_URLS:
        try:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                },
                timeout=15.0,
            )
            if resp.status_code != 200:
                logger.debug(f"Slug harvest: {url} returned {resp.status_code}")
                continue

            matches = _ATS_SLUG_RE.findall(resp.text)
            for slug in matches:
                slug = slug.lower().strip().rstrip("/")
                if not slug or slug in ("embed", "v1", "api", "www"):
                    continue

                is_gh = "boards.greenhouse.io" in resp.text.lower()
                is_lev = "jobs.lever.co" in resp.text.lower() or "lever.co" in resp.text.lower()
                is_ash = "ashbyhq.com" in resp.text.lower()

                if is_gh and slug not in _SLUG_STORE["greenhouse"]:
                    _SLUG_STORE["greenhouse"].add(slug)
                    new_slugs += 1
                if is_lev and slug not in _SLUG_STORE["lever"]:
                    _SLUG_STORE["lever"].add(slug)
                    new_slugs += 1
                if is_ash and slug not in _SLUG_STORE["ashby"]:
                    _SLUG_STORE["ashby"].add(slug)
                    new_slugs += 1
        except Exception as exc:
            logger.debug(f"Slug harvest failed for {url}: {exc}")

    _SLUG_LAST_HARVEST = time.monotonic()
    _SLUG_COUNT = sum(len(v) for v in _SLUG_STORE.values())
    logger.info(f"Slug harvest: {new_slugs} new slugs (total: {_SLUG_COUNT} across 3 platforms)")
    return new_slugs


async def get_all_slugs() -> dict[str, set[str]]:
    """Return the current slug database. Triggers harvest if empty."""
    total = sum(len(v) for v in _SLUG_STORE.values())
    if total == 0:
        await harvest_slugs_from_github_readmes()
    return _SLUG_STORE


def _extract_greenhouse_jobs(data: Any, slug: str) -> list[dict[str, Any]]:
    """Parse a Greenhouse board payload; raise ValueError on bad structure."""
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("expected object with a 'jobs' array")
    jobs: list[dict[str, Any]] = []
    for item in data["jobs"]:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        if not _TITLE_KEYWORDS.search(title):
            continue
        updated = item.get("updated_at", "")
        if not _is_within_48h(updated):
            continue
        jobs.append(
            {
                "url": item.get("absolute_url", ""),
                "title": title,
                "location": (item.get("location") or {}).get("name", ""),
                "updated_at": updated,
                "platform": "greenhouse",
                "slug": slug,
            }
        )
    return jobs


def _extract_lever_jobs(data: Any, slug: str) -> list[dict[str, Any]]:
    """Parse a Lever postings payload; raise ValueError on bad structure."""
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of postings")
    jobs: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = item.get("text", "")
        if not _TITLE_KEYWORDS.search(title):
            continue
        created = item.get("createdAt", "")
        if not _is_within_48h(created):
            continue
        jobs.append(
            {
                "url": item.get("hostedUrl") or item.get("applyUrl", ""),
                "title": title,
                "location": (item.get("categories") or {}).get("location", ""),
                "updated_at": created,
                "platform": "lever",
                "slug": slug,
            }
        )
    return jobs


def _extract_ashby_jobs(data: Any, slug: str) -> list[dict[str, Any]]:
    """Parse an Ashby board payload; raise ValueError on bad structure."""
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("expected object with a 'jobs' array")
    jobs: list[dict[str, Any]] = []
    for item in data["jobs"]:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        if not _TITLE_KEYWORDS.search(title):
            continue
        published = item.get("publishedAt", "")
        if not _is_within_48h(published):
            continue
        job_id = item.get("id", "")
        job_url = f"https://jobs.ashbyhq.com/{slug}/{job_id}" if job_id else ""
        jobs.append(
            {
                "url": job_url,
                "title": title,
                "location": item.get("locationName", ""),
                "updated_at": published,
                "platform": "ashby",
                "slug": slug,
            }
        )
    return jobs


async def _poll_platform_slugs(
    platform: str,
    slugs: Iterable[str],
    method: str,
    url_template: str,
    extract: Callable[[Any, str], list[dict[str, Any]]],
    request_kwargs: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[BoardPollResult]]:
    """Poll every slug for one platform, recording a per-board outcome.

    Slugs are normalized and deduplicated first, so a given slug is requested
    at most once per round. A failing board never interrupts the others.
    """
    unique_slugs = sorted({s.strip().lower() for s in slugs if s and s.strip()})
    jobs: list[dict[str, Any]] = []
    results: list[BoardPollResult] = []
    sem = asyncio.Semaphore(30)

    async def _fetch(slug: str) -> None:
        started = time.monotonic()

        def _record(outcome: str, reason: str = "", jobs_found: int = 0) -> None:
            results.append(
                BoardPollResult(
                    platform=platform,
                    slug=slug,
                    outcome=outcome,
                    reason=reason,
                    jobs_found=jobs_found,
                    duration_s=time.monotonic() - started,
                )
            )

        async with sem:
            try:
                client = await get_client("ats_mass_poller", timeout=25.0)
                resp = await _timed_request(
                    client,
                    method,
                    url_template.format(slug=slug),
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "Accept": "application/json",
                    },
                    **(request_kwargs or {}),
                )
            except TimeoutError:
                _record(OUTCOME_TIMEOUT, f"exceeded {MASS_POLL_REQUEST_TIMEOUT:.0f}s hard cap")
                return
            except Exception as exc:
                _record(OUTCOME_REQUEST_ERROR, f"{type(exc).__name__}: {exc}")
                return

            if resp.status_code != 200:
                _record(OUTCOME_HTTP_ERROR, f"HTTP {resp.status_code}")
                return

            try:
                data = resp.json()
            except Exception as exc:
                _record(OUTCOME_INVALID_RESPONSE, f"body is not valid JSON: {exc}")
                return

            try:
                matched = extract(data, slug)
            except Exception as exc:
                _record(OUTCOME_INVALID_RESPONSE, f"unexpected payload structure: {exc}")
                return

            jobs.extend(matched)
            if matched:
                _record(OUTCOME_MATCHED, jobs_found=len(matched))
            else:
                _record(OUTCOME_NO_MATCH)

    tasks = [asyncio.create_task(_fetch(s)) for s in unique_slugs]
    await asyncio.gather(*tasks, return_exceptions=True)
    return jobs, results


async def _poll_greenhouse_slugs(
    slugs: Iterable[str],
) -> tuple[list[dict[str, Any]], list[BoardPollResult]]:
    """Ping all Greenhouse slugs concurrently and filter by freshness + title."""
    return await _poll_platform_slugs("greenhouse", slugs, "get", GREENHOUSE_API, _extract_greenhouse_jobs)


async def _poll_lever_slugs(
    slugs: Iterable[str],
) -> tuple[list[dict[str, Any]], list[BoardPollResult]]:
    """Ping all Lever slugs concurrently and filter by freshness + title."""
    return await _poll_platform_slugs("lever", slugs, "get", LEVER_API, _extract_lever_jobs)


async def _poll_ashby_slugs(
    slugs: Iterable[str],
) -> tuple[list[dict[str, Any]], list[BoardPollResult]]:
    """Ping all Ashby slugs concurrently and filter by freshness + title."""
    return await _poll_platform_slugs(
        "ashby",
        slugs,
        "post",
        ASHBY_API,
        _extract_ashby_jobs,
        request_kwargs={"json": {"includeCompensation": True}},
    )


def _is_within_48h(timestamp: str) -> bool:
    """Check if a UTC timestamp is within the last 48 hours."""
    if not timestamp:
        return False
    try:
        from datetime import UTC, datetime, timedelta

        ts = timestamp.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
            ):
                try:
                    dt = datetime.strptime(ts, fmt)
                    break
                except ValueError:
                    continue
            else:
                return False

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        cutoff = datetime.now(UTC) - timedelta(hours=48)
        return dt > cutoff
    except Exception:
        return False


async def poll_all_mass_slugs() -> tuple[list[JobObservation], MassPollSummary]:
    """Ping the full slug database across all 3 ATS platforms.

    Harvests slugs if needed, then concurrently polls Greenhouse, Lever,
    and Ashby endpoints. Only returns jobs with intern/grad/junior titles
    posted within the last 48 hours.

    Returns a tuple of (JobObservations ready for gating and LLM matching,
    a MassPollSummary with per-outcome counts, the failed-board list
    (platform + slug + reason), and round timing). A platform whose task
    raises is recorded in summary.platform_errors without discarding the
    other platforms' results.
    """
    slugs = await get_all_slugs()
    total_slugs = sum(len(v) for v in slugs.values())
    if total_slugs == 0:
        logger.warning("Mass poller: no slugs available")
        return [], MassPollSummary()

    logger.info(f"Mass poller: polling {total_slugs} slugs across 3 platforms...")
    t0 = time.monotonic()

    gh_task = asyncio.create_task(_poll_greenhouse_slugs(slugs.get("greenhouse", set())))
    lv_task = asyncio.create_task(_poll_lever_slugs(slugs.get("lever", set())))
    ab_task = asyncio.create_task(_poll_ashby_slugs(slugs.get("ashby", set())))

    results = await asyncio.gather(gh_task, lv_task, ab_task, return_exceptions=True)

    all_jobs: list[dict[str, Any]] = []
    board_results: list[BoardPollResult] = []
    platform_errors: list[str] = []
    for platform, r in zip(("greenhouse", "lever", "ashby"), results, strict=True):
        if isinstance(r, BaseException):
            logger.warning(f"Mass poller: {platform} platform task failed: {r}")
            platform_errors.append(f"{platform}: {type(r).__name__}: {r}")
            continue
        platform_jobs, platform_boards = r
        all_jobs.extend(platform_jobs)
        board_results.extend(platform_boards)

    elapsed = time.monotonic() - t0
    summary = MassPollSummary.build(
        board_results=board_results,
        jobs_found=len(all_jobs),
        elapsed_s=elapsed,
        platform_errors=platform_errors,
    )
    logger.info(
        f"Mass poller complete: {len(all_jobs)} fresh junior-job hits from "
        f"{summary.total_boards - len(summary.failures)}/{summary.total_boards} boards "
        f"in {elapsed:.1f}s (outcomes={summary.outcomes}, "
        f"failures={len(summary.failures)}, platform_errors={len(platform_errors)})"
    )
    for failure in summary.failures:
        logger.debug(
            f"Mass poller board failure: {failure.platform}/{failure.slug} -> "
            f"{failure.outcome} ({failure.reason})"
        )

    observations: list[JobObservation] = []
    for item in all_jobs:
        url = item.get("url", "")
        if not url or not url.startswith("http"):
            continue
        obs = JobObservation(
            url=url,
            source=f"mass_poller:{item.get('platform', 'unknown')}",
            title=item.get("title", ""),
            snippet=(
                f"Location: {item.get('location', 'Remote')} | "
                f"ATS: {item.get('platform', '')} | "
                f"Posted: {item.get('updated_at', '')}"
            ),
            raw_markdown=item.get("raw_markdown", ""),
            source_freshness_evidence=item.get("updated_at", ""),
        )
        obs.extra["official_source"] = True
        obs.extra["ats_platform"] = item.get("platform", "")
        obs.extra["company_slug"] = item.get("slug", "")
        obs.extra["mass_polled"] = True
        observations.append(obs)

    return observations, summary


async def mass_poll_loop(
    interval_seconds: float = 14400,
    callback=None,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Run a continuous loop that polls all ATS slugs every 4 hours.

    Args:
        interval_seconds: Poll interval (default 4 hours = 14400s).
        callback: Async callable receiving list[JobObservation] from each poll.
        shutdown_event: Event to signal shutdown.
    """
    first_run = True
    while True:
        if shutdown_event and shutdown_event.is_set():
            return

        try:
            if first_run:
                await harvest_slugs_from_github_readmes()
                first_run = False

            jobs, summary = await poll_all_mass_slugs()
            if summary.failures:
                logger.info(
                    f"Mass poll round: {len(summary.failures)} failed boards "
                    f"(outcomes={summary.outcomes})"
                )
            if jobs and callback:
                await callback(jobs)
        except Exception as exc:
            logger.warning(f"Mass poll loop iteration failed: {exc}")

        if shutdown_event:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval_seconds)
                return
            except TimeoutError:
                continue
        else:
            await asyncio.sleep(interval_seconds)
