"""End-to-end tests driving the CLI entry point with local HTML stubs."""

import json
import logging

import pytest
import requests

from amazon_product_details_scraper import app

from conftest import (
    BAD_DOMAIN_URL,
    PRODUCT_URL,
    FakeResponse,
    build_product_html,
    install_stub_get,
)


def test_app_url_list_with_blank_lines_and_mixed_outcomes(
    monkeypatch, tmp_path, caplog
):
    list_file = tmp_path / "urls.txt"
    list_file.write_text(
        f"  {PRODUCT_URL}  \n\n   \n{BAD_DOMAIN_URL}\n", encoding="utf8"
    )
    output_dir = tmp_path / "out"

    pages = {
        PRODUCT_URL: FakeResponse(build_product_html("B000CLI01")),
    }
    install_stub_get(monkeypatch, pages)

    monkeypatch.setattr(
        "sys.argv",
        [
            "amazon-scraper",
            "--url-list",
            str(list_file),
            "--output-dir",
            str(output_dir),
        ],
    )

    with caplog.at_level(logging.INFO):
        app.main()

    # Only the two non-empty lines were processed; blank lines never became URLs
    with open(output_dir / "batch-summary.json", encoding="utf8") as f:
        summary = json.load(f)
    assert summary["total_urls"] == 2
    assert [entry["url"] for entry in summary["entries"]] == [
        PRODUCT_URL,
        BAD_DOMAIN_URL,
    ]
    assert summary["succeeded"] == 1
    assert summary["failed"] == 1

    # The completion log line carries the counts
    assert any("Batch complete" in record.message for record in caplog.records)


def test_app_single_url_mode_also_writes_summary(monkeypatch, tmp_path):
    output_dir = tmp_path / "single-out"
    pages = {PRODUCT_URL: FakeResponse(build_product_html("B000CLI02"))}
    install_stub_get(monkeypatch, pages)

    monkeypatch.setattr(
        "sys.argv",
        [
            "amazon-scraper",
            "--url",
            PRODUCT_URL,
            "--output-dir",
            str(output_dir),
        ],
    )
    app.main()

    with open(output_dir / "batch-summary.json", encoding="utf8") as f:
        summary = json.load(f)
    assert summary["total_urls"] == 1
    assert summary["entries"][0]["status"] == "success"


def test_app_missing_url_list_file_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        [
            "amazon-scraper",
            "--url-list",
            str(tmp_path / "does-not-exist.txt"),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        app.main()
    assert exc_info.value.code == 1


def test_no_real_network_is_ever_called(monkeypatch, tmp_path):
    calls = []

    def fail_if_called(*args, **kwargs):
        # Record the attempted call instead of performing any real I/O
        calls.append(args)
        raise requests.ConnectionError("network disabled in tests")

    monkeypatch.setattr(requests, "get", fail_if_called)
    monkeypatch.setattr(
        "sys.argv",
        [
            "amazon-scraper",
            "--url",
            "https://www.amazon.com/dp/B000STUB",
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    app.main()

    # The stub intercepted the request; no real network call could happen
    assert len(calls) == 1
    assert calls[0][0] == "https://www.amazon.com/dp/B000STUB"

    with open(tmp_path / "out" / "batch-summary.json", encoding="utf8") as f:
        summary = json.load(f)
    assert summary["failed"] == 1
    assert "network disabled in tests" in summary["entries"][0]["error"]
