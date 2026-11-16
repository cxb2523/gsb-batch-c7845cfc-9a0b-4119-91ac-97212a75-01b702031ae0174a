# coding=utf-8
"""Collect Disqus comments for many articles, settling each one independently.

This module is the reusable entry point that sits between the existing steps
and the Scrapy spiders. It keeps their responsibilities untouched:

* ``url_grabber``  - find the Disqus iframe address on the article page
* ``json_grabber`` - fetch the iframe page and save its thread payload
* ``get_posts.py`` - parse the thread payload and write posts to MongoDB

For every article it settles on exactly one outcome:

* ``success``              - comments were written (0 comments is still success)
* ``no_iframe``            - the article page has no Disqus iframe
* ``thread_json_missing``  - the embed page has no ``disqus-threadData`` payload
* ``thread_json_invalid``  - the payload is not parseable / lacks posts
* ``write_failed``         - MongoDB refused the write
* ``request_failed``       - fetching the article or embed page failed

Processing continues with the next article after any outcome. Comments are
upserted per article as soon as they are parsed, so a later failure can never
roll back or duplicate an earlier article's comments.

Usage from a shell (``main.sh`` does exactly this):

    python collect.py --urls-file articles.txt
    python collect.py URL1 URL2 --mongo-uri mongodb://localhost:27017

The same work is available programmatically through ``collect_articles`` /
``collect_articles_async``; either accepts a custom ``crawler`` and ``store``
so tests can run against local fixtures and an in-memory MongoDB.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import get_posts

# Each article settles on exactly one of these outcomes.
SUCCESS = "success"
NO_IFRAME = "no_iframe"
THREAD_JSON_MISSING = "thread_json_missing"
THREAD_JSON_INVALID = "thread_json_invalid"
WRITE_FAILED = "write_failed"
REQUEST_FAILED = "request_failed"

FAILURE_OUTCOMES = (
    NO_IFRAME,
    THREAD_JSON_MISSING,
    THREAD_JSON_INVALID,
    WRITE_FAILED,
    REQUEST_FAILED,
)

SPIDERS_PACKAGE = "disqus-crawler.spiders"
SETTINGS_MODULE = "disqus-crawler.settings"
DEFAULT_WORK_DIR = "collect-work"
REPO_ROOT = Path(__file__).resolve().parent


def _ensure_project_on_path() -> None:
    """Make the Scrapy project importable regardless of the current directory."""
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    import os

    if not Path.cwd().joinpath("scrapy.cfg").exists():
        os.environ.setdefault("SCRAPY_SETTINGS_MODULE", SETTINGS_MODULE)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class StepResult:
    """Outcome of running one spider step."""

    status: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class ArticleResult:
    """Final, per-article settlement of one collection attempt."""

    article_url: str
    outcome: str
    detail: str = ""
    iframe_url: Optional[str] = None
    thread_file: Optional[str] = None
    comments: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.outcome == SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "article_url": self.article_url,
            "outcome": self.outcome,
            "detail": self.detail,
            "iframe_url": self.iframe_url,
            "thread_file": self.thread_file,
            "comments": self.comments,
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "error": self.error,
        }

    def summary_line(self) -> str:
        if self.outcome == SUCCESS:
            return "%s  comments=%d (inserted=%d updated=%d unchanged=%d)" % (
                self.outcome,
                self.comments,
                self.inserted,
                self.updated,
                self.unchanged,
            )
        text = self.outcome
        if self.detail:
            text += " (%s)" % self.detail
        if self.error and self.error not in text:
            text += " [%s]" % self.error
        return text


# ---------------------------------------------------------------------------
# Crawling steps (reuse the existing spiders, one step at a time)
# ---------------------------------------------------------------------------
def install_reactor() -> None:
    """Install Scrapy's asyncio reactor once, before any event loop runs."""
    from scrapy.utils.reactor import install_reactor as _install, is_reactor_installed

    if not is_reactor_installed():
        _install()


def load_spider(module_suffix: str, class_name: str):
    _ensure_project_on_path()
    module = importlib.import_module("%s.%s" % (SPIDERS_PACKAGE, module_suffix))
    return getattr(module, class_name)


class ScrapyStepCrawler:
    """Runs ``url_grabber`` / ``json_grabber`` in-process, one step at a time.

    The spiders write their own JSON status file (see spiders/common.py), which
    is more reliable than checking Scrapy's exit code: callbacks that raise
    still leave the process exit code at 0.
    """

    def __init__(self, settings: Any = None, log_level: str = "WARNING") -> None:
        from scrapy.settings import Settings

        if settings is None:
            _ensure_project_on_path()
            from scrapy.utils.project import get_project_settings

            settings = get_project_settings()
            if not isinstance(settings, Settings):
                settings = Settings(settings)
        self.settings = settings
        self.log_level = log_level
        self._runner = None

    def _get_runner(self):
        from scrapy.crawler import CrawlerRunner

        if self._runner is None:
            self.settings.set("LOG_LEVEL", self.log_level, priority="cmdline")
            self.settings.set("LOGSTATS_INTERVAL", 0, priority="cmdline")
            self.settings.set("TELNETCONSOLE_ENABLED", False, priority="cmdline")
            # Scrapy 2.20+ starts a local HTTP control server per process; a
            # batch of short-lived one-step crawls does not need it.
            self.settings.set("REMOTE_CONTROL_ENABLED", False, priority="cmdline")
            self._runner = CrawlerRunner(self.settings)
        return self._runner

    async def _crawl(self, module_suffix: str, class_name: str, **spider_kwargs) -> None:
        from twisted.internet.defer import Deferred

        loop = asyncio.get_running_loop()
        crawler = self._get_runner().create_crawler(load_spider(module_suffix, class_name))
        deferred: Deferred = self._get_runner().crawl(crawler, **spider_kwargs)
        await deferred.asFuture(loop)

    @staticmethod
    def _read_status(status_path: Path) -> StepResult:
        _ensure_project_on_path()
        common = importlib.import_module("%s.common" % SPIDERS_PACKAGE)
        status = common.read_status(str(status_path))
        if not status:
            return StepResult(
                status="error",
                error="spider produced no status file (%s)" % status_path,
            )
        return StepResult(
            status=status.get("status", "error"),
            data=status,
            error=status.get("error"),
        )

    async def find_iframe_url(
        self, article_url: str, out_path: Path, status_path: Path
    ) -> StepResult:
        try:
            await self._crawl(
                "url_grabber",
                "UrlGrabberSpider",
                url=article_url,
                out=str(out_path),
                status=str(status_path),
            )
        except Exception as exc:  # crawler bootstrap failure
            return StepResult(
                status="error", error="%s: %s" % (type(exc).__name__, exc)
            )
        return self._read_status(status_path)

    async def fetch_thread_json(
        self, iframe_url: str, out_path: Path, status_path: Path
    ) -> StepResult:
        try:
            await self._crawl(
                "json_grabber",
                "JsonGrabberSpider",
                url=iframe_url,
                out=str(out_path),
                status=str(status_path),
            )
        except Exception as exc:  # crawler bootstrap failure
            return StepResult(
                status="error", error="%s: %s" % (type(exc).__name__, exc)
            )
        return self._read_status(status_path)


# ---------------------------------------------------------------------------
# Per-article collection
# ---------------------------------------------------------------------------
def article_slug(article_url: str) -> str:
    """Stable directory name for one article (same URL -> same directory)."""
    trimmed = article_url.rstrip("/")
    tail = trimmed.rsplit("/", 1)[-1]
    tail = re.sub(r"[^A-Za-z0-9._-]+", "-", tail).strip("-.") or "article"
    digest = hashlib.sha1(article_url.encode("utf-8")).hexdigest()[:8]
    return "%s-%s" % (tail[:50], digest)


async def collect_article(
    article_url: str,
    store: Any,
    work_dir: Path,
    crawler: Any,
) -> ArticleResult:
    """Collect one article, always returning a settled :class:`ArticleResult`."""
    article_dir = Path(work_dir) / article_slug(article_url)
    article_dir.mkdir(parents=True, exist_ok=True)
    url_file = article_dir / "url.txt"
    thread_file = article_dir / "thread.json"
    url_status = article_dir / "url.status.json"
    thread_status = article_dir / "thread.status.json"

    # Step 1: locate the Disqus iframe.
    try:
        url_step = await crawler.find_iframe_url(article_url, url_file, url_status)
    except Exception as exc:
        return ArticleResult(
            article_url,
            REQUEST_FAILED,
            detail="url_grabber step failed",
            error="%s: %s" % (type(exc).__name__, exc),
        )

    if url_step.status == NO_IFRAME:
        return ArticleResult(
            article_url,
            NO_IFRAME,
            detail=url_step.data.get("detail", "no Disqus iframe found"),
        )
    if url_step.status != "found":
        return ArticleResult(
            article_url,
            REQUEST_FAILED,
            detail="url_grabber step failed",
            error=url_step.error or "unexpected step status: %s" % url_step.status,
        )

    iframe_url = url_step.data.get("iframe")
    if not iframe_url:
        try:
            iframe_url = url_file.read_text(encoding="utf-8").strip()
        except OSError:
            iframe_url = None
    if not iframe_url:
        return ArticleResult(
            article_url,
            REQUEST_FAILED,
            detail="url_grabber reported an iframe but wrote no url.txt",
        )

    # Step 2: fetch the thread payload from the embed page.
    try:
        json_step = await crawler.fetch_thread_json(iframe_url, thread_file, thread_status)
    except Exception as exc:
        return ArticleResult(
            article_url,
            REQUEST_FAILED,
            detail="json_grabber step failed",
            iframe_url=iframe_url,
            error="%s: %s" % (type(exc).__name__, exc),
        )

    if json_step.status == "missing":
        return ArticleResult(
            article_url,
            THREAD_JSON_MISSING,
            detail=json_step.data.get("detail", "embed page has no thread data"),
            iframe_url=iframe_url,
        )
    if json_step.status != "saved":
        return ArticleResult(
            article_url,
            REQUEST_FAILED,
            detail="json_grabber step failed",
            iframe_url=iframe_url,
            error=json_step.error or "unexpected step status: %s" % json_step.status,
        )

    # Step 3: parse the payload. Empty posts is a valid, zero-comment success.
    try:
        posts = get_posts.load_posts(thread_file)
    except get_posts.ThreadJsonError as exc:
        return ArticleResult(
            article_url,
            THREAD_JSON_INVALID,
            detail=str(exc),
            iframe_url=iframe_url,
            thread_file=str(thread_file),
        )

    # Step 4: write the comments idempotently. Failures here do not affect
    # comments already written for this or other articles.
    try:
        counters = store.upsert_posts(posts)
    except get_posts.CommentWriteError as exc:
        return ArticleResult(
            article_url,
            WRITE_FAILED,
            detail=str(exc),
            iframe_url=iframe_url,
            thread_file=str(thread_file),
            error=str(exc),
        )
    except Exception as exc:
        return ArticleResult(
            article_url,
            WRITE_FAILED,
            detail="unexpected error while writing comments",
            iframe_url=iframe_url,
            thread_file=str(thread_file),
            error="%s: %s" % (type(exc).__name__, exc),
        )

    return ArticleResult(
        article_url,
        SUCCESS,
        iframe_url=iframe_url,
        thread_file=str(thread_file),
        comments=counters["comments"],
        inserted=counters["inserted"],
        updated=counters["updated"],
        unchanged=counters["unchanged"],
    )


async def collect_articles_async(
    articles: Sequence[str],
    store: Any,
    work_dir: str | Path = DEFAULT_WORK_DIR,
    crawler: Any = None,
    on_result: Optional[Callable[[ArticleResult], None]] = None,
) -> List[ArticleResult]:
    """Collect every article independently; never stop after a failure."""
    if crawler is None:
        crawler = ScrapyStepCrawler()
    work_path = Path(work_dir)
    work_path.mkdir(parents=True, exist_ok=True)

    results: List[ArticleResult] = []
    for article_url in articles:
        result = await collect_article(article_url, store, work_path, crawler)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results


def collect_articles(
    articles: Sequence[str],
    store: Any,
    work_dir: str | Path = DEFAULT_WORK_DIR,
    crawler: Any = None,
    on_result: Optional[Callable[[ArticleResult], None]] = None,
) -> List[ArticleResult]:
    """Synchronous wrapper around :func:`collect_articles_async`.

    Installs Scrapy's asyncio reactor (once) and runs one event loop for the
    whole batch, so each article is still crawled and settled in order.
    """
    install_reactor()
    return asyncio.run(
        collect_articles_async(
            articles,
            store=store,
            work_dir=work_dir,
            crawler=crawler,
            on_result=on_result,
        )
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def read_article_urls(
    positional: Iterable[str], urls_file: Optional[str]
) -> List[str]:
    """Merge positional URLs with a one-URL-per-line ``--urls-file``."""
    urls: List[str] = []
    for url in positional or ():
        url = url.strip()
        if url:
            urls.append(url)

    if urls_file:
        try:
            lines = Path(urls_file).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise SystemExit("cannot read urls file %s: %s" % (urls_file, exc))
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)

    # Preserve first-seen order while dropping exact duplicates.
    seen = set()
    unique = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect Disqus comments for each article and settle each one "
            "independently (success / no_iframe / thread_json_missing / "
            "thread_json_invalid / write_failed / request_failed)."
        )
    )
    parser.add_argument("urls", nargs="*", help="article URLs to collect")
    parser.add_argument(
        "--urls-file",
        default=None,
        help="file with one article URL per line (blank lines and # comments ignored)",
    )
    parser.add_argument(
        "--work-dir",
        default=DEFAULT_WORK_DIR,
        help="directory for per-article url.txt/thread.json (default: %(default)s)",
    )
    parser.add_argument("--mongo-uri", default=None, help="MongoDB connection URI")
    parser.add_argument("--db", default=get_posts.DEFAULT_DB, help="database name")
    parser.add_argument(
        "--collection",
        default=get_posts.DEFAULT_COLLECTION,
        help="collection name",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="optional path for a JSON report of every article's outcome",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Scrapy log level for the crawl steps (default: %(default)s)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    articles = read_article_urls(args.urls, args.urls_file)
    if not articles:
        print("No article URLs given (use positional URLs or --urls-file).", file=sys.stderr)
        return 2

    store = get_posts.MongoCommentStore(
        uri=args.mongo_uri, db_name=args.db, collection_name=args.collection
    )
    crawler = ScrapyStepCrawler(log_level=args.log_level)

    def report_line(result: ArticleResult) -> None:
        print("ARTICLE %s -> %s" % (result.article_url, result.summary_line()))
        sys.stdout.flush()

    results = collect_articles(
        articles, store=store, work_dir=args.work_dir, crawler=crawler,
        on_result=report_line,
    )

    try:
        total = store.count_comments()
    except get_posts.CommentWriteError as exc:
        total = None
        print("WARNING: could not count comments: %s" % exc, file=sys.stderr)
    finally:
        store.close()

    print("")
    print("SUMMARY")
    for result in results:
        print("  %-85s %s" % (result.article_url, result.summary_line()))
    succeeded = sum(1 for r in results if r.ok)
    print(
        "  %d/%d articles succeeded, %d failed; %s comments in %s.%s"
        % (
            succeeded,
            len(results),
            len(results) - succeeded,
            "?" if total is None else total,
            args.db,
            args.collection,
        )
    )

    if args.report:
        payload = {
            "articles": [r.to_dict() for r in results],
            "total_in_collection": total,
        }
        Path(args.report).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
