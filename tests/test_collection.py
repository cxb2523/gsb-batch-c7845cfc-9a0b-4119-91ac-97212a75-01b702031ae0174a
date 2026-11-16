# coding=utf-8
"""Regression tests for the per-article Disqus collection.

These run entirely offline: the crawler steps are replaced by a fake that
reads the bundled ``samples/`` files, and MongoDB is replaced by mongomock.
Run with ``python -m pytest tests`` from the repository root.
"""

import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import collect  # noqa: E402
import get_posts  # noqa: E402

SAMPLES = REPO_ROOT / "samples"
ARTICLES = SAMPLES / "articles"


def file_url(path: Path) -> str:
    return path.resolve().as_uri()


def path_from_url(url: str) -> Path:
    return Path(url2pathname(unquote(urlparse(url).path)))


def write_status(path, **payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class OfflineCrawler:
    """Stand-in for ``ScrapyStepCrawler`` that resolves local fixture files.

    It follows the same contract (a status file plus an output file per step)
    so the collector's settlement logic is exercised without Scrapy.
    """

    def __init__(self):
        self.url_calls = []
        self.json_calls = []

    async def find_iframe_url(self, article_url, out_path, status_path):
        from parsel import Selector

        self.url_calls.append(article_url)
        path = path_from_url(article_url)
        if not path.exists():
            return collect.StepResult(
                status="error", error="FileNotFoundError: %s" % path
            )
        selector = Selector(text=path.read_text(encoding="utf-8"))
        iframes = selector.xpath(
            "//iframe["
            "contains(@src,'http://disqus.com') or "
            "contains(@src,'https://disqus.com') or "
            "contains(@src,'//disqus.com') or "
            "@title='Disqus' or "
            "contains(@id,'dsq')]"
        )
        srcs = [s.strip() for s in iframes.xpath("@src").extract() if s.strip()]
        if not srcs:
            write_status(status_path, status="no_iframe", url=article_url)
            return collect.StepResult(status="no_iframe", data={"status": "no_iframe"})
        iframe_url = str((path.parent / srcs[0]).resolve())
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(iframe_url, encoding="utf-8")
        write_status(status_path, status="found", url=article_url, iframe=iframe_url)
        return collect.StepResult(status="found", data={"iframe": iframe_url})

    async def fetch_thread_json(self, iframe_url, out_path, status_path):
        from parsel import Selector

        self.json_calls.append(iframe_url)
        path = Path(iframe_url)
        if not path.exists():
            return collect.StepResult(
                status="error", error="FileNotFoundError: %s" % path
            )
        selector = Selector(text=path.read_text(encoding="utf-8"))
        payloads = selector.xpath("//script[@id='disqus-threadData']/text()").extract()
        payload = payloads[0] if payloads else ""
        if not payload.strip():
            write_status(status_path, status="missing", url=iframe_url)
            return collect.StepResult(status="missing", data={"status": "missing"})
        Path(out_path).write_text(payload, encoding="utf-8")
        write_status(status_path, status="saved", url=iframe_url)
        return collect.StepResult(status="saved", data={"status": "saved"})


class FailingCrawler:
    def __init__(self, fail_at="json"):
        self.fail_at = fail_at

    async def find_iframe_url(self, *args, **kwargs):
        if self.fail_at == "url":
            return collect.StepResult(status="error", error="download failed")
        return collect.StepResult(
            status="found", data={"iframe": file_url(ARTICLES / "article-ok.html")}
        )

    async def fetch_thread_json(self, *args, **kwargs):
        return collect.StepResult(status="error", error="download failed")


class FlakyStore:
    """Store that fails its first N writes, then succeeds."""

    def __init__(self, failures=1):
        self.failures = failures
        self.calls = 0

    def upsert_posts(self, posts):
        self.calls += 1
        if self.calls <= self.failures:
            raise get_posts.CommentWriteError("simulated outage")
        return {
            "comments": len(posts),
            "inserted": len(posts),
            "updated": 0,
            "unchanged": 0,
        }


@pytest.fixture()
def store():
    mongomock = pytest.importorskip("mongomock")
    return get_posts.MongoCommentStore(client=mongomock.MongoClient(), db_name="t")


def run(articles, store, crawler, work_dir, on_result=None):
    return collect.collect_articles(
        articles, store=store, work_dir=str(work_dir), crawler=crawler, on_result=on_result
    )


def test_each_article_settles_independently(store, tmp_path):
    cases = {
        "normal": (ARTICLES / "article-ok.html", collect.SUCCESS),
        "empty": (ARTICLES / "article-empty-thread.html", collect.SUCCESS),
        "no_iframe": (ARTICLES / "article-no-disqus.html", collect.NO_IFRAME),
        "corrupt": (ARTICLES / "article-corrupt-thread.html", collect.THREAD_JSON_INVALID),
        "missing": (ARTICLES / "article-missing-thread.html", collect.THREAD_JSON_MISSING),
    }
    urls = [file_url(path) for path, _ in cases.values()]

    results = run(urls, store, OfflineCrawler(), tmp_path)

    assert [r.outcome for r in results] == [
        collect.SUCCESS,
        collect.SUCCESS,
        collect.NO_IFRAME,
        collect.THREAD_JSON_INVALID,
        collect.THREAD_JSON_MISSING,
    ]
    assert results[0].comments == 3
    # An iframe with an empty posts list is a successful zero-comment article.
    assert results[1].comments == 0
    assert store.count_comments() == 3


def test_failure_does_not_stop_later_articles(store, tmp_path):
    urls = [file_url(ARTICLES / "article-no-disqus.html"), file_url(ARTICLES / "article-ok.html")]

    results = run(urls, store, OfflineCrawler(), tmp_path)

    assert results[0].outcome == collect.NO_IFRAME
    assert results[1].outcome == collect.SUCCESS
    assert store.count_comments() == 3


def test_recollecting_is_idempotent(store, tmp_path):
    url = file_url(ARTICLES / "article-ok.html")
    run([url], store, OfflineCrawler(), tmp_path)
    second = run([url], store, OfflineCrawler(), tmp_path)[0]

    assert second.outcome == collect.SUCCESS
    assert second.comments == 3
    assert second.inserted == 0
    assert second.unchanged == 3
    assert store.count_comments() == 3


def test_write_failure_settles_and_continues(store, tmp_path):
    urls = [file_url(ARTICLES / "article-ok.html"), file_url(ARTICLES / "article-empty-thread.html")]

    failing = FlakyStore(failures=1)
    results = run(urls, failing, OfflineCrawler(), tmp_path)

    assert results[0].outcome == collect.WRITE_FAILED
    assert "simulated outage" in results[0].error
    assert results[1].outcome == collect.SUCCESS
    assert failing.calls == 2
    # Nothing reached MongoDB for the failed article.
    assert store.count_comments() == 0


def test_request_failure_is_reported(store, tmp_path):
    url = file_url(ARTICLES / "article-ok.html")
    results = run([url], store, FailingCrawler(fail_at="url"), tmp_path)
    assert results[0].outcome == collect.REQUEST_FAILED

    results = run([url], store, FailingCrawler(fail_at="json"), tmp_path)
    assert results[0].outcome == collect.REQUEST_FAILED
    assert results[0].iframe_url


def test_on_result_called_per_article(store, tmp_path):
    seen = []
    urls = [file_url(ARTICLES / "article-ok.html"), file_url(ARTICLES / "article-no-disqus.html")]
    run(urls, store, OfflineCrawler(), tmp_path, on_result=seen.append)
    assert [r.outcome for r in seen] == [collect.SUCCESS, collect.NO_IFRAME]


def test_parse_errors_are_classified():
    bad_payloads = [
        "",
        "{not json",
        '{"response": {}}',
        '{"response": {"posts": "nope"}}',
        '{"response": {"posts": [42]}}',
    ]
    for payload in bad_payloads:
        with pytest.raises(get_posts.ThreadJsonError):
            get_posts.parse_posts_from_text(payload)


def test_posts_without_id_are_deduped_by_content(store):
    posts = [{"message": "one"}, {"message": "two"}]
    assert store.upsert_posts(posts)["inserted"] == 2
    assert store.upsert_posts(posts)["unchanged"] == 2
    assert store.count_comments() == 2


def test_store_requires_no_database_for_empty_post_list():
    class Exploding:
        def __getitem__(self, name):
            raise AssertionError("MongoDB must not be touched for an empty thread")

    store = get_posts.MongoCommentStore(client=Exploding(), db_name="x")
    assert store.upsert_posts([])["comments"] == 0


def test_read_article_urls_merges_and_dedupes(tmp_path):
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("# comment\n\nhttp://a\nhttp://b\nhttp://a\n", encoding="utf-8")
    urls = collect.read_article_urls(["http://c", ""], str(urls_file))
    assert urls == ["http://c", "http://a", "http://b"]
