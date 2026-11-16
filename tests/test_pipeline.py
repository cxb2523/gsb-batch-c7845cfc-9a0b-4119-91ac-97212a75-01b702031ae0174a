"""End-to-end pipeline behaviour for batches with unreachable URLs."""

import asyncio
import json

import pytest

from airbyte_connector_generator_poc import main as skyffel_main
from airbyte_connector_generator_poc.errors import ScrapeBatchFailedError
from airbyte_connector_generator_poc.report import PageReport
from airbyte_connector_generator_poc.scraper import ScrapeResult

from .conftest import FakePage, FakeResponse, md5_of

URL_OK_1 = "https://docs.example.com/intro"
URL_BROKEN = "https://unreachable.example.com/nope"
URL_OK_2 = "https://docs.example.com/search"

PAGE_TEMPLATE = """
<html><body>
  <nav id="nav">Navigation</nav>
  <main id="content"><h1>{heading}</h1><p>{body}</p></main>
  <footer id="footer">Footer</footer>
</body></html>
"""
HTML_OK_1 = PAGE_TEMPLATE.format(heading="Intro", body="Intro body")
HTML_OK_2 = PAGE_TEMPLATE.format(heading="Search", body="Search body")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def isolated_workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fake_scraper(monkeypatch):
    """Patch scrape_urls with a fake batch taking the real failure logic."""

    def install(result: ScrapeResult):
        async def fake_scrape_urls(urls, report=None, timeout_ms=0):
            return result

        monkeypatch.setattr(skyffel_main, "scrape_urls", fake_scrape_urls)
        return result

    return install


@pytest.fixture
def fake_openapi(monkeypatch):
    captured = {}

    async def fake_generate(url_html_documents, user_goal, page_report=None):
        captured["documents"] = dict(url_html_documents)
        captured["goal"] = user_goal
        return {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1.0.0"},
            "paths": {},
        }

    monkeypatch.setattr(
        "airbyte_connector_generator_poc.openapi_generator.generate_openapi_spec",
        fake_generate)
    return captured


def _success_result():
    report = PageReport([URL_OK_1, URL_BROKEN, URL_OK_2])
    report.record_scrape_success(URL_OK_1)
    report.record_scrape_failure(URL_BROKEN, "net::ERR_NAME_NOT_RESOLVED")
    report.record_scrape_success(URL_OK_2)
    # Preprocessing is simulated as successful for the scraped pages.
    report.record_preprocessing_success(URL_OK_1)
    report.record_preprocessing_success(URL_OK_2)
    return ScrapeResult({URL_OK_1: HTML_OK_1, URL_OK_2: HTML_OK_2}, report)


def test_partial_failure_still_produces_openapi_and_report(
        fake_scraper, fake_openapi, tmp_path):
    fake_scraper(_success_result())

    spec = _run(skyffel_main.build_openapi_spec("goal", [
        URL_OK_1, URL_BROKEN, URL_OK_2]))

    assert spec["info"]["title"] == "T"
    # Only the successful pages - and in order - reach generation.
    assert list(fake_openapi["documents"].keys()) == [URL_OK_1, URL_OK_2]

    report_json = json.loads(
        (tmp_path / ".skyffel" / "scrape_report.json").read_text("utf-8"))
    pages = {page["url"]: page for page in report_json["pages"]}
    assert html_pages(pages) == [URL_OK_1, URL_OK_2]
    assert pages[URL_BROKEN]["status"] == "failed"
    assert pages[URL_BROKEN]["error"] == "net::ERR_NAME_NOT_RESOLVED"
    assert pages[URL_BROKEN]["used_for_generation"] is False
    assert report_json["summary"]["succeeded"] == 2
    assert report_json["summary"]["failed"] == 1


def html_pages(pages: dict) -> list:
    return [url for url, page in pages.items() if page["status"] == "success"]


def test_all_pages_failing_raises_and_writes_no_openapi(
        fake_scraper, fake_openapi, tmp_path):
    report = PageReport([URL_OK_1, URL_BROKEN])
    report.record_scrape_failure(URL_OK_1, "HTTP 500 Internal Server Error")
    report.record_scrape_failure(URL_BROKEN, "timeout after 30000ms")
    fake_scraper(ScrapeResult({}, report))

    with pytest.raises(ScrapeBatchFailedError) as excinfo:
        _run(skyffel_main.build_openapi_spec("goal", [URL_OK_1, URL_BROKEN]))

    assert excinfo.value.report is report
    assert not (tmp_path / "openapi.yaml").exists()

    report_json = json.loads(
        (tmp_path / ".skyffel" / "scrape_report.json").read_text("utf-8"))
    assert report_json["summary"]["failed"] == 2
    assert report_json["summary"]["succeeded"] == 0
    assert "documents" not in fake_openapi


def test_stale_outputs_are_removed_before_a_failing_run(
        fake_scraper, fake_openapi, tmp_path, monkeypatch):
    (tmp_path / "openapi.yaml").write_text("stale: true\n", encoding="utf-8")
    (tmp_path / "airbyte_connector.yaml").write_text(
        "stale: true\n", encoding="utf-8")

    report = PageReport([URL_BROKEN])
    report.record_scrape_failure(URL_BROKEN, "net::ERR_CONNECTION_REFUSED")
    fake_scraper(ScrapeResult({}, report))

    # Mirror _main's cleanup + failure path without touching OpenAI/Airbyte.
    skyffel_main.remove_stale_output_files()
    with pytest.raises(ScrapeBatchFailedError):
        _run(skyffel_main.build_openapi_spec("goal", [URL_BROKEN]))

    assert not (tmp_path / "openapi.yaml").exists()
    assert not (tmp_path / "airbyte_connector.yaml").exists()


def test_invalid_url_only_fails_that_page(fake_openapi, tmp_path, monkeypatch):
    report = PageReport([URL_OK_1, "not a url", URL_OK_2])
    report.record_scrape_success(URL_OK_1)
    report.record_scrape_success(URL_OK_2)
    report.record_preprocessing_success(URL_OK_1)
    report.record_preprocessing_success(URL_OK_2)
    result = ScrapeResult({URL_OK_1: HTML_OK_1, URL_OK_2: HTML_OK_2}, report)

    recorded = {}

    async def fake_scrape_urls(urls, report=None, timeout_ms=0):
        recorded["urls"] = list(urls)
        return result

    monkeypatch.setattr(skyffel_main, "scrape_urls", fake_scrape_urls)

    _run(skyffel_main.build_openapi_spec("goal", [URL_OK_1, "not a url", URL_OK_2]))

    # Only the valid URLs were handed to the scraper...
    assert recorded["urls"] == [URL_OK_1, URL_OK_2]
    # ...while the invalid one is reported as a per-page failure.
    assert report.get("not a url").error == "invalid URL"
    assert report.get("not a url").status == "failed"


def test_all_urls_invalid_raises_without_generation(fake_openapi):
    with pytest.raises(ScrapeBatchFailedError):
        _run(skyffel_main.build_openapi_spec("goal", ["not a url"]))
    assert "documents" not in fake_openapi


def test_report_lists_successes_and_failures_in_debug_files(
        fake_scraper, fake_openapi, tmp_path):
    fake_scraper(_success_result())

    _run(skyffel_main.build_openapi_spec("goal", [
        URL_OK_1, URL_BROKEN, URL_OK_2]))

    markdown = (tmp_path / ".skyffel" / "scrape_report.md").read_text("utf-8")
    assert URL_BROKEN in markdown
    assert "failed" in markdown
    assert URL_OK_1 in markdown and URL_OK_2 in markdown

    report_json = json.loads(
        (tmp_path / ".skyffel" / "scrape_report.json").read_text("utf-8"))
    statuses = {page["url"]: page["status"] for page in report_json["pages"]}
    assert statuses == {
        URL_OK_1: "success",
        URL_BROKEN: "failed",
        URL_OK_2: "success",
    }
