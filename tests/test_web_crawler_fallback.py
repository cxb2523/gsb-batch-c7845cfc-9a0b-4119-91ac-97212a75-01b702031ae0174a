import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from selenium.common.exceptions import WebDriverException

import contents
from contents import (
    CrawlResult,
    SOURCE_BROWSER,
    SOURCE_STATIC,
    WebCrawlError,
    describe_source,
    web_crawler_newspaper,
)

ARTICLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>A Local Test Article</title></head>
<body>
<article>
<h1>A Local Test Article</h1>
<p>The quick brown fox jumps over the lazy dog. This sentence exists so that the readability
extractor has a real paragraph to keep when it looks for the main body of the page.</p>
<p>Static requests should be able to retrieve this text even though no browser is rendering it.
The weather in the valley was mild and the river ran quietly past the old mill.</p>
<p>Every paragraph here is long enough to be treated as article content rather than a menu item
or a footer link, which keeps the test focused on the fallback logic itself.</p>
</article>
</body>
</html>
"""


class _ArticleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ARTICLE_HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def article_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), _ArticleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/article.html'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def broken_browser(monkeypatch):
    """Simulate an unusable browser, e.g. an invalid chromedriver path."""

    def _boom(*args, **kwargs):
        raise WebDriverException(
            "Message: 'chromedriver' executable needs to be in PATH; the configured driver "
            "path does not point to a valid chromedriver binary")

    monkeypatch.setattr(contents.webdriver, 'Chrome', _boom)


@pytest.fixture()
def working_browser(monkeypatch):
    """Simulate a browser that renders the local page, without depending on a real browser."""
    fake = _FakeDriver(ARTICLE_HTML)
    monkeypatch.setattr(contents.webdriver, 'Chrome', lambda *a, **k: fake)
    monkeypatch.setattr(contents.time, 'sleep', lambda seconds: None)
    return fake


class _FakeDriver:
    def __init__(self, html):
        self._html = html
        self.requested_url = None
        self.quit_called = False

    def get(self, url):
        self.requested_url = url

    @property
    def page_source(self):
        return self._html

    def quit(self):
        self.quit_called = True


def test_static_fallback_when_browser_is_unavailable(article_server, broken_browser):
    result = web_crawler_newspaper(article_server)

    assert isinstance(result, CrawlResult)
    assert result.source == SOURCE_STATIC
    assert result.lang == 'en'
    assert result.contents
    assert any('quick brown fox' in line for line in result.contents)


def test_both_paths_fail_reports_both_stages(broken_browser):
    # A port nothing listens on makes the static request fail as well.
    dead_url = 'http://127.0.0.1:1/article.html'

    with pytest.raises(WebCrawlError) as excinfo:
        web_crawler_newspaper(dead_url)

    message = str(excinfo.value)
    assert 'Browser rendering stage failed' in message
    assert 'chromedriver' in message
    assert 'Static request stage failed' in message
    assert dead_url in message
    assert isinstance(excinfo.value.browser_error, WebDriverException)
    assert excinfo.value.static_error is not None


def test_static_http_error_reports_both_stages(article_server, broken_browser, monkeypatch):
    def _failing_get(*args, **kwargs):
        raise contents.requests.ConnectionError('connection refused by the local test server')

    monkeypatch.setattr(contents.requests, 'get', _failing_get)

    with pytest.raises(WebCrawlError) as excinfo:
        web_crawler_newspaper(article_server)

    message = str(excinfo.value)
    assert 'Browser rendering stage failed' in message
    assert 'Static request stage failed' in message
    assert 'connection refused' in message


def test_static_empty_content_is_treated_as_failure(article_server, broken_browser, monkeypatch):
    # The static page loads, but no article text can be extracted from it.
    monkeypatch.setattr(contents, '_extract_contents', lambda readable_html, lang, url: [])

    with pytest.raises(WebCrawlError) as excinfo:
        web_crawler_newspaper(article_server)

    message = str(excinfo.value)
    assert 'Browser rendering stage failed' in message
    assert 'Static request stage failed' in message
    assert 'produced no article content' in message


def test_browser_path_reports_browser_source(article_server, working_browser):
    result = web_crawler_newspaper(article_server)

    assert result.source == SOURCE_BROWSER
    assert result.contents
    assert any('quick brown fox' in line for line in result.contents)
    assert working_browser.requested_url == article_server
    assert working_browser.quit_called


def test_browser_driver_is_quit_when_navigation_fails(article_server, monkeypatch):
    fake = _FakeDriver(ARTICLE_HTML)

    def _failing_get(url):
        raise RuntimeError('navigation timed out')

    fake.get = _failing_get
    monkeypatch.setattr(contents.webdriver, 'Chrome', lambda *a, **k: fake)
    monkeypatch.setattr(contents.time, 'sleep', lambda seconds: None)

    with pytest.raises(WebCrawlError) as excinfo:
        web_crawler_newspaper('http://127.0.0.1:1/article.html')

    assert fake.quit_called
    assert 'navigation timed out' in str(excinfo.value.browser_error)


def test_empty_browser_result_falls_back_to_static(article_server, monkeypatch):
    # The browser starts and returns a document, but no article text can be extracted from it.
    monkeypatch.setattr(contents, '_get_readable_html_from_browser',
                        lambda url: ('<html><body></body></html>', 'en'))

    result = web_crawler_newspaper(article_server)

    assert result.source == SOURCE_STATIC
    assert any('quick brown fox' in line for line in result.contents)


def test_static_source_is_labelled_as_degraded():
    assert describe_source(SOURCE_BROWSER) == 'browser rendering'
    assert describe_source(SOURCE_STATIC) == 'static HTTP request'
    assert SOURCE_BROWSER != SOURCE_STATIC


@pytest.mark.skipif(os.environ.get('CHATWEB_BROWSER_TEST') != '1',
                    reason='set CHATWEB_BROWSER_TEST=1 to exercise a real headless browser')
def test_real_headless_browser(article_server):
    result = web_crawler_newspaper(article_server)

    assert result.source == SOURCE_BROWSER
    assert any('quick brown fox' in line for line in result.contents)
