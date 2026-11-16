"""Unit tests for the per-page report data model."""

import json

from airbyte_connector_generator_poc.report import (
    PageReport,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
)

URL_OK = "https://docs.example.com/intro"
URL_BAD = "https://docs.example.com/broken"


def test_scraped_but_unprocessed_page_is_pending_not_used():
    report = PageReport([URL_OK])
    report.record_scrape_success(URL_OK)

    outcome = report.get(URL_OK)
    assert outcome.status == STATUS_PENDING
    assert outcome.used_for_generation is False
    assert report.successful_pages == []


def test_scrape_failure_marks_page_failed_and_skipped_for_preprocessing():
    report = PageReport([URL_BAD])
    report.record_scrape_failure(URL_BAD, "HTTP 500 Internal Server Error")

    outcome = report.get(URL_BAD)
    assert outcome.status == STATUS_FAILED
    assert outcome.failed_stage == "scrape"
    assert outcome.preprocessing_status == STATUS_SKIPPED
    assert outcome.error == "HTTP 500 Internal Server Error"


def test_preprocessing_failure_keeps_scrape_outcome():
    report = PageReport([URL_BAD])
    report.record_scrape_success(URL_BAD)
    report.record_preprocessing_failure(URL_BAD, "No main content found")

    outcome = report.get(URL_BAD)
    assert outcome.status == STATUS_FAILED
    assert outcome.failed_stage == "preprocess"
    assert outcome.used_for_generation is False


def test_successful_page_reports_cache_hit_detail():
    report = PageReport([URL_OK])
    report.record_scrape_success(URL_OK)
    report.record_preprocessing_success(URL_OK, cache_hit=True)

    outcome = report.get(URL_OK)
    assert outcome.status == STATUS_SUCCESS
    assert outcome.used_for_generation is True
    assert outcome.detail == "cache hit"


def test_summary_counts_and_order_are_preserved():
    report = PageReport([URL_OK, URL_BAD])
    report.record_scrape_success(URL_OK)
    report.record_scrape_failure(URL_BAD, "timeout")
    report.record_preprocessing_success(URL_OK)

    assert [o.url for o in report.outcomes] == [URL_OK, URL_BAD]
    assert report.summary() == {
        "total": 2, "succeeded": 1, "failed": 1, "pending": 0, "skipped": 0}


def test_write_produces_json_and_markdown(tmp_path):
    report = PageReport([URL_OK, URL_BAD])
    report.record_scrape_success(URL_OK)
    report.record_scrape_failure(URL_BAD, "HTTP 404 | Not Found")

    json_path, markdown_path = report.write(directory=str(tmp_path / ".skyffel"))

    data = json.loads(open(json_path, encoding="utf-8").read())
    assert data["summary"]["failed"] == 1
    assert [p["url"] for p in data["pages"]] == [URL_OK, URL_BAD]

    markdown = open(markdown_path, encoding="utf-8").read()
    assert URL_OK in markdown and URL_BAD in markdown
    # Pipes inside a failure reason must not break the markdown table.
    assert "HTTP 404 \\| Not Found" in markdown


def test_print_summary_mentions_failed_urls(capsys):
    from rich.console import Console

    report = PageReport([URL_OK, URL_BAD])
    report.record_scrape_success(URL_OK)
    report.record_preprocessing_success(URL_OK)
    report.record_scrape_failure(URL_BAD, "net::ERR_NAME_NOT_RESOLVED")

    console = Console(file=None, force_terminal=False, width=160)
    report.print_summary(console)

    output = capsys.readouterr().out
    assert URL_OK in output
    assert URL_BAD in output
    assert "net::ERR_NAME_NOT_RESOLVED" in output
    assert "1/2" in output
