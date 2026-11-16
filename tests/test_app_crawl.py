"""App-level tests for the site-crawl panel.

These run the real Streamlit page through ``streamlit.testing`` with an
injected fake HTTP fetcher, so no network access (and no Ollama/Chroma install)
is required.
"""

import json
import os
import sys
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crawler  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = "https://en.wikipedia.org/"


class FakeResponse:
    def __init__(self, html, status_code=200):
        self.content = html.encode("utf-8")
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code} error")


def make_html(title, body, links=()):
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    return (
        "<html><head>"
        f"<title>{title}</title>"
        "</head><body>"
        f"<h1>{body}</h1>"
        f"{anchors}"
        "</body></html>"
    )


class FakeWiki:
    """In-memory site whose Nth request blocks until ``release`` is set."""

    def __init__(self, pages, failing=(), block_at=None, release=None):
        self.pages = pages
        self.failing = set(failing)
        self.block_at = block_at
        self.release = release
        self.requested = []
        self._lock = threading.Lock()

    def __call__(self, url):
        with self._lock:
            self.requested.append(url)
            index = len(self.requested)
        if self.block_at is not None and index == self.block_at:
            assert self.release is not None
            self.release.wait(timeout=30)
        if url in self.failing:
            raise OSError(f"boom for {url}")
        if url in self.pages:
            return FakeResponse(self.pages[url])
        return FakeResponse("<html><body>404</body></html>", status_code=404)


WIKI_PAGES = {
    START: make_html(
        "Wikipedia",
        "welcome to wikipedia",
        ["/wiki/Python", "/wiki/History", "/wiki/Missing"],
    ),
    "https://en.wikipedia.org/wiki/Python": make_html(
        "Python", "python is a language", ["/wiki/Programming"]
    ),
    "https://en.wikipedia.org/wiki/History": make_html("History", "history page"),
    "https://en.wikipedia.org/wiki/Programming": make_html(
        "Programming", "programming page"
    ),
}


def make_app_test():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=90)


def runner_from(at):
    return at.session_state["crawl_runner"]


def wait_for_runner_end(at, timeout=30):
    runner = runner_from(at)
    thread = getattr(runner, "_thread", None)
    if thread is not None:
        thread.join(timeout=timeout)
    at.run()
    return runner


def test_page_renders_crawl_entry_even_without_rag_stack(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    at = make_app_test()
    at.run()

    assert not at.exception, [e.value for e in at.exception]
    assert any(s.value == "站点采集" for s in at.subheader)
    assert at.button(key="crawl_start").label == "开始采集"


def test_full_crawl_shows_counts_failures_and_writes_json(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    site = FakeWiki(WIKI_PAGES)
    monkeypatch.setattr(crawler, "fetch_url", site)

    at = make_app_test()
    at.run()
    at.button(key="crawl_start").click().run()

    runner = wait_for_runner_end(at)
    assert not runner.is_alive()

    session = runner.session
    assert session.success_count == 4
    assert session.failure_count == 1
    assert session.total_visited == 5

    metrics = {m.label: m.value for m in at.metric}
    assert metrics["已访问页数"] == "5"
    assert metrics["成功"] == "4"
    assert metrics["失败"] == "1"

    markdown_text = "\n".join(m.value for m in at.markdown)
    assert "https://en.wikipedia.org/wiki/Missing" in markdown_text
    assert "404" in markdown_text

    data_path = tmp_path / "scraped_content.json"
    records = json.loads(data_path.read_text(encoding="utf-8"))
    assert len(records) == 4
    assert all(set(record) == {"url", "title", "content"} for record in records)


def test_stop_then_continue_from_page_does_not_refetch(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    release = threading.Event()
    # The 2nd request hangs, which gives the test a stable window in which the
    # crawl is definitely still running while we press 停止.
    site = FakeWiki(WIKI_PAGES, block_at=2, release=release)
    monkeypatch.setattr(crawler, "fetch_url", site)

    at = make_app_test()
    at.run()
    at.button(key="crawl_start").click().run()

    runner = runner_from(at)
    deadline = time.time() + 15
    while len(site.requested) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert len(site.requested) == 2, "crawl worker did not reach the 2nd request"
    assert runner.is_alive()

    at.button(key="crawl_stop").click().run()
    assert runner.session.status == "stopped"
    release.set()
    runner = wait_for_runner_end(at)

    fetched_first_round = list(site.requested)
    assert runner.session.status == "stopped"
    assert runner.session.visited == fetched_first_round
    assert (
        runner.session.success_count + runner.session.failure_count
        == len(fetched_first_round)
    )
    assert runner.saved is True

    at.run()
    at.button(key="crawl_resume").click().run()
    runner = wait_for_runner_end(at)

    assert runner.session.status == "completed"
    assert len(site.requested) == len(set(site.requested)), "page fetched twice"
    assert runner.session.total_visited == 5
    assert runner.session.success_count == 4
    assert runner.session.failure_count == 1

    metrics = {m.label: m.value for m in at.metric}
    assert metrics["已访问页数"] == "5"
    assert metrics["成功"] == "4"
    assert metrics["失败"] == "1"


def test_no_successful_page_keeps_old_data_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    data_path = tmp_path / "scraped_content.json"
    old = [{"url": "https://old.test/", "title": "Old", "content": "old content"}]
    data_path.write_text(json.dumps(old), encoding="utf-8")
    before = data_path.read_bytes()

    site = FakeWiki({}, failing={START})
    monkeypatch.setattr(crawler, "fetch_url", site)

    at = make_app_test()
    at.run()
    at.button(key="crawl_start").click().run()
    runner = wait_for_runner_end(at)

    assert runner.session.success_count == 0
    assert runner.session.failure_count == 1
    assert data_path.read_bytes() == before

    warnings = " ".join(w.value for w in at.warning)
    assert "未写入" in warnings
    errors = " ".join(e.value for e in at.error)
    assert "START_URL" in errors


def test_ingest_error_is_shown_on_page(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    site = FakeWiki(WIKI_PAGES)
    monkeypatch.setattr(crawler, "fetch_url", site)

    def failing_ingest(*args, **kwargs):
        raise RuntimeError("ollama is not running")

    monkeypatch.setattr("ingest.ingest_data", failing_ingest)

    at = make_app_test()
    at.run()
    at.button(key="crawl_start").click().run()
    wait_for_runner_end(at)

    at.button(key="ingest_button").click().run()

    errors = " ".join(e.value for e in at.error)
    assert "入库失败" in errors
    assert "ollama is not running" in errors
