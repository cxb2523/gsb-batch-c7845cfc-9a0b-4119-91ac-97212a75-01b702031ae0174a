"""Batch crawl-to-index settlement: per-page outcomes and aggregate counts."""

from __future__ import annotations

import requests

from retriever.indexer import Document, IngestStatus
from retriever.web import (
    CrawlReport,
    PageOutcome,
    WebCrawler,
    crawl_and_index,
)

from conftest import FakeRobots, FakeSite, make_crawler

DOC_ALPHA = "Alpha page explains vector search and ranking pipelines in detail."
DOC_BETA = "Beta page documents chunk overlap strategies for retrieval quality."
DOC_GAMMA = "Gamma page covers robots compliance, throttling, and politeness."


def html(body: str, links: list[str] | tuple[str, ...] = ()) -> str:
    anchors = "".join(f'<a href="{href}">link</a>' for href in links)
    return f"<html><body><main><p>{body}</p></main>{anchors}</body></html>"


def outcomes(report: CrawlReport) -> dict[str, PageOutcome]:
    return {page.url: page.outcome for page in report}


def test_successful_pages_are_indexed_with_chunk_counts(index, fake_robots):
    site = FakeSite({
        "https://site.test/a": html(DOC_ALPHA),
        "https://site.test/b": html(DOC_BETA),
    })
    crawler = make_crawler(site, fake_robots, max_pages=10)

    report = crawler.crawl_and_index(
        ["https://site.test/a", "https://site.test/b"], index
    )

    assert report.summary == {
        "indexed": 2, "robots_denied": 0, "fetch_failed": 0,
        "duplicate": 0, "unprocessed": 0, "total": 2,
    }
    assert all(page.chunks > 0 for page in report.pages)
    assert report.total_chunks == index.stats()["chunks"]
    assert report.pages[0].status_code == 200
    assert outcomes(report) == {
        "https://site.test/a": PageOutcome.INDEXED,
        "https://site.test/b": PageOutcome.INDEXED,
    }
    assert index.stats()["documents"] == 2


def test_single_page_http_failure_does_not_abort_batch(index, fake_robots):
    error = requests.HTTPError(
        "500 Server Error",
        response=_response(500),
    )
    site = FakeSite(
        {
            "https://site.test/ok1": html(DOC_ALPHA),
            # /broken fails, /ok2 must still be processed afterwards
            "https://site.test/ok2": html(DOC_BETA),
        },
        errors={"https://site.test/broken": error},
    )
    crawler = make_crawler(site, fake_robots, max_pages=10)

    report = crawler.crawl_and_index(
        ["https://site.test/ok1", "https://site.test/broken",
         "https://site.test/ok2"],
        index,
    )

    assert report.summary["indexed"] == 2
    assert report.summary["fetch_failed"] == 1
    assert report.summary["total"] == 3

    broken = next(p for p in report if p.url.endswith("/broken"))
    assert broken.outcome is PageOutcome.FETCH_FAILED
    assert broken.status_code == 500
    assert broken.error and "500" in broken.error
    assert broken.chunks == 0

    # Meanwhile the successful pages survived and are searchable.
    assert index.stats()["documents"] == 2
    assert index.retrieve(["vector"])


def test_timeout_is_reported_as_fetch_failure(index, fake_robots):
    site = FakeSite(
        {"https://site.test/ok": html(DOC_ALPHA)},
        errors={"https://site.test/slow": requests.Timeout("read timed out")},
    )
    crawler = make_crawler(site, fake_robots, max_pages=10)

    report = crawler.crawl_and_index(
        ["https://site.test/slow", "https://site.test/ok"], index
    )

    slow = next(p for p in report if p.url.endswith("/slow"))
    assert slow.outcome is PageOutcome.FETCH_FAILED
    assert slow.status_code is None
    assert "timeout" in (slow.error or "")
    assert report.summary["indexed"] == 1


def test_robots_denied_pages_are_reported_and_skipped(index):
    robots = FakeRobots(denied={"https://site.test/private"})
    site = FakeSite({
        "https://site.test/public": html(DOC_ALPHA),
        "https://site.test/private": html(DOC_BETA),
    })
    crawler = make_crawler(site, robots, max_pages=10)

    report = crawler.crawl_and_index(
        ["https://site.test/public", "https://site.test/private"], index
    )

    assert outcomes(report)["https://site.test/private"] is PageOutcome.ROBOTS_DENIED
    denied = report.denied_pages[0]
    assert denied.status_code is None
    assert denied.chunks == 0
    assert report.summary["robots_denied"] == 1
    assert report.summary["indexed"] == 1
    # Denied URLs are never fetched.
    assert "https://site.test/private" not in site.requested
    assert index.stats()["documents"] == 1


def test_duplicate_content_indexed_once_and_linked_to_first_url(index, fake_robots):
    site = FakeSite({
        "https://site.test/original": html(DOC_ALPHA),
        "https://site.test/copy": html(DOC_ALPHA),  # identical text
        "https://site.test/other": html(DOC_BETA),
    })
    crawler = make_crawler(site, fake_robots, max_pages=10)

    report = crawler.crawl_and_index(
        ["https://site.test/original", "https://site.test/copy",
         "https://site.test/other"],
        index,
    )

    by_url = {p.url: p for p in report}
    assert by_url["https://site.test/original"].outcome is PageOutcome.INDEXED
    dup = by_url["https://site.test/copy"]
    assert dup.outcome is PageOutcome.DUPLICATE
    assert dup.duplicate_of == "https://site.test/original"
    assert dup.chunks == 0
    assert dup.status_code == 200
    assert report.summary["duplicate"] == 1
    assert report.summary["indexed"] == 2
    # Only two documents' worth of chunks exist.
    assert index.stats()["documents"] == 2
    assert report.total_chunks == index.stats()["chunks"]


def test_max_pages_truncates_and_marks_remainder_unprocessed(index, fake_robots):
    site = FakeSite({
        "https://site.test/1": html(DOC_ALPHA),
        "https://site.test/2": html(DOC_BETA),
        "https://site.test/3": html(DOC_GAMMA),
        "https://site.test/4": html("Delta page should never be fetched."),
    })
    crawler = make_crawler(site, fake_robots, max_pages=2)

    report = crawler.crawl_and_index(
        ["https://site.test/1", "https://site.test/2",
         "https://site.test/3", "https://site.test/4"],
        index,
    )

    assert report.summary == {
        "indexed": 2, "robots_denied": 0, "fetch_failed": 0,
        "duplicate": 0, "unprocessed": 2, "total": 4,
    }
    unprocessed = report.unprocessed_pages
    assert {p.url for p in unprocessed} == {
        "https://site.test/3", "https://site.test/4",
    }
    assert all(p.chunks == 0 and p.status_code is None for p in unprocessed)
    # Unprocessed URLs are not fetched or indexed.
    assert site.requested == ["https://site.test/1", "https://site.test/2"]
    assert index.stats()["documents"] == 2


def test_max_pages_counts_successful_fetches_not_failures(index):
    site = FakeSite(
        {
            "https://site.test/1": html(DOC_ALPHA),
            "https://site.test/3": html(DOC_BETA),
            "https://site.test/5": html(DOC_GAMMA),
        },
        errors={"https://site.test/2": requests.ConnectionError("refused")},
    )
    robots = FakeRobots(denied={"https://site.test/4"})
    crawler = make_crawler(site, robots, max_pages=2)

    report = crawler.crawl_and_index(
        ["https://site.test/1", "https://site.test/4",
         "https://site.test/2", "https://site.test/3",
         "https://site.test/5"],
        index,
    )

    # Only successful fetches consume the budget: page 4 was denied, page 2
    # failed, so pages 1 and 3 fill it and page 5 stays untouched.
    assert report.summary == {
        "indexed": 2, "robots_denied": 1, "fetch_failed": 1,
        "duplicate": 0, "unprocessed": 1, "total": 5,
    }
    assert outcomes(report)["https://site.test/5"] is PageOutcome.UNPROCESSED
    assert "https://site.test/5" not in site.requested


def test_bfs_follows_links_and_reports_whole_frontier(index, fake_robots):
    site = FakeSite({
        "https://site.test/root": html(DOC_ALPHA, links=[
            "/child1", "/child2",
        ]),
        "https://site.test/child1": html(DOC_BETA),
        "https://site.test/child2": html(DOC_GAMMA),
    })
    crawler = make_crawler(site, fake_robots, max_pages=10, max_depth=2)

    report = crawler.crawl_and_index(["https://site.test/root"], index)

    assert report.summary["indexed"] == 3
    assert {p.depth for p in report.pages} == {0, 1}
    assert index.stats()["documents"] == 3


def test_robots_check_error_marks_denied_and_continues(index):
    class ExplodingRobots:
        def allowed(self, url, agent="*"):
            if "boom" in url:
                raise OSError("robots.txt unreachable in a bad way")
            return True

    site = FakeSite({
        "https://site.test/boom": html(DOC_BETA),
        "https://site.test/fine": html(DOC_ALPHA),
    })
    crawler = make_crawler(site, ExplodingRobots(), max_pages=10)

    report = crawler.crawl_and_index(
        ["https://site.test/boom", "https://site.test/fine"], index
    )

    boom = next(p for p in report if p.url.endswith("/boom"))
    assert boom.outcome is PageOutcome.ROBOTS_DENIED
    assert "robots" in (boom.error or "")
    assert report.summary["indexed"] == 1


def test_fetcher_returning_none_reports_fetch_failure(index, fake_robots):
    site = FakeSite({"https://site.test/present": html(DOC_ALPHA)})
    crawler = make_crawler(site, fake_robots, max_pages=10)

    report = crawler.crawl_and_index(
        ["https://site.test/absent", "https://site.test/present"], index
    )

    assert report.summary["fetch_failed"] == 1
    assert report.summary["indexed"] == 1
    assert report.failed_pages[0].chunks == 0


def test_duplicate_within_same_batch_links_back_across_depths(index, fake_robots):
    # Empty anchor text keeps the extracted body identical between pages.
    site = FakeSite({
        "https://site.test/root": (
            f"<html><body><main><p>{DOC_ALPHA}</p></main>"
            '<a href="/child"></a></body></html>'
        ),
        "https://site.test/child": html(DOC_ALPHA),
    })
    crawler = make_crawler(site, fake_robots, max_pages=10, max_depth=2)

    report = crawler.crawl_and_index(["https://site.test/root"], index)

    child = next(p for p in report if p.url.endswith("/child"))
    assert child.outcome is PageOutcome.DUPLICATE
    assert child.duplicate_of == "https://site.test/root"
    assert child.depth == 1
    assert report.summary["duplicate"] == 1


def test_legacy_crawl_returns_successful_pages_only(index, fake_robots):
    site = FakeSite(
        {"https://site.test/ok": html(DOC_ALPHA)},
        errors={"https://site.test/bad": requests.HTTPError(
            "404", response=_response(404))},
    )
    crawler = make_crawler(site, fake_robots, max_pages=10)

    pages = crawler.crawl(["https://site.test/ok", "https://site.test/bad"])

    assert [page.url for page in pages] == ["https://site.test/ok"]
    assert pages[0].status_code == 200


def test_module_level_helper_matches_method(index, fake_robots):
    site = FakeSite({"https://site.test/only": html(DOC_ALPHA)})
    report = crawl_and_index(
        ["https://site.test/only"], index,
        fetcher=site.fetch, robots=fake_robots, delay_ms=0, max_pages=5,
    )
    assert report.summary["indexed"] == 1
    assert report.pages[0].status_code == 200


# ---------------------------------------------------------------------------
# Real requests.Session path (monkeypatched; no network access)
# ---------------------------------------------------------------------------
def test_real_session_records_http_error_status(index, fake_robots, monkeypatch):
    crawler = WebCrawler(robots=fake_robots, delay_ms=0, max_pages=10)
    html_body = html(DOC_ALPHA)

    def fake_get(url, timeout=None):
        response = _response(404)
        response.url = url
        response._content = b"<html><body>not found</body></html>"
        response.encoding = "utf-8"
        response.headers["Content-Type"] = "text/html"
        return response

    monkeypatch.setattr(crawler.session, "get", fake_get)

    report = crawler.crawl_and_index(["https://site.test/missing"], index)

    page = report.pages[0]
    assert page.outcome is PageOutcome.FETCH_FAILED
    assert page.status_code == 404
    assert report.summary["fetch_failed"] == 1
    assert index.stats()["documents"] == 0
    assert html_body  # sanity: fixture body unrelated to this path


def test_real_session_timeout_recorded(index, fake_robots, monkeypatch):
    crawler = WebCrawler(robots=fake_robots, delay_ms=0, max_pages=10)

    def fake_get(url, timeout=None):
        raise requests.Timeout("read timed out")

    monkeypatch.setattr(crawler.session, "get", fake_get)

    report = crawler.crawl_and_index(["https://site.test/slow"], index)

    page = report.pages[0]
    assert page.outcome is PageOutcome.FETCH_FAILED
    assert page.status_code is None
    assert "timeout" in (page.error or "")
    assert report.summary["fetch_failed"] == 1


# ---------------------------------------------------------------------------
# Indexer-level behaviour (single-document ingest must not regress)
# ---------------------------------------------------------------------------
def test_indexer_single_document_add_still_returns_chunk_count(index):
    chunks = index.add([Document(id="doc-1", text=DOC_ALPHA)])
    assert chunks > 0
    assert index.stats()["documents"] == 1


def test_indexer_reports_duplicate_with_first_source(index):
    first = Document(id="doc-1", url="https://site.test/first", text=DOC_ALPHA)
    second = Document(id="doc-2", url="https://site.test/second", text=DOC_ALPHA)

    results = index.add_with_report([first, second])

    assert results[0].status is IngestStatus.INDEXED
    assert results[0].chunks > 0
    assert results[1].status is IngestStatus.DUPLICATE
    assert results[1].chunks == 0
    assert results[1].duplicate_of == "https://site.test/first"
    # The legacy add() path must not double-count the duplicate.
    assert index.add([second]) == 0


def test_mixed_batch_summary_matches_per_page_outcomes(index):
    robots = FakeRobots(denied={"https://site.test/nope"})
    site = FakeSite(
        {
            "https://site.test/one": html(DOC_ALPHA),
            "https://site.test/two": html(DOC_ALPHA),      # duplicate
            "https://site.test/three": html(DOC_BETA),
        },
        errors={"https://site.test/down": requests.HTTPError(
            "503", response=_response(503))},
    )
    crawler = make_crawler(site, robots, max_pages=3)

    report = crawler.crawl_and_index(
        ["https://site.test/one", "https://site.test/down",
         "https://site.test/two", "https://site.test/three",
         "https://site.test/nope"],
        index,
    )

    # Budget of 3 successes fills before /nope is reached, so it settles as
    # unprocessed instead of a robots denial.
    assert report.summary == {
        "indexed": 2, "robots_denied": 0, "fetch_failed": 1,
        "duplicate": 1, "unprocessed": 1, "total": 5,
    }
    assert sum(report.summary[k] for k in
               ("indexed", "robots_denied", "fetch_failed", "duplicate",
                "unprocessed")) == report.summary["total"]
    assert report.total_chunks == index.stats()["chunks"]


def _response(status_code: int) -> requests.Response:
    """Build a minimal requests Response carrying ``status_code``."""
    response = requests.Response()
    response.status_code = status_code
    response.url = "https://site.test/"
    return response
