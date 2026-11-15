"""Bad-link paths: invalid domain, request exception and missing title.

Each failing URL must be marked failed with a reason while the other URLs in
the same batch keep running, and every URL (failed or not) must appear in the
summary.
"""

import os

import requests

from amazon_product_details_scraper.config import DEFAULT_OUTPUT_FILENAME
from amazon_product_details_scraper.core.batch import process_url_batch

from conftest import (
    BAD_DOMAIN_URL,
    MISSING_TITLE_URL,
    PRODUCT_URL,
    REQUEST_ERROR_URL,
    FakeResponse,
    build_product_html,
    install_stub_get,
)


def test_invalid_domain_request_error_and_missing_title(monkeypatch, output_dir):
    pages = {
        PRODUCT_URL: FakeResponse(
            build_product_html("B000GOOD01", image_urls=[])
        ),
        REQUEST_ERROR_URL: requests.ConnectionError("network down"),
        # Valid Amazon domain and HTTP 200, but no product title on the page
        MISSING_TITLE_URL: FakeResponse(
            build_product_html("B000NOTITLE", title=None)
        ),
    }
    install_stub_get(monkeypatch, pages)

    url_list = [
        BAD_DOMAIN_URL,
        REQUEST_ERROR_URL,
        MISSING_TITLE_URL,
        PRODUCT_URL,
    ]
    summary = process_url_batch(url_list, str(output_dir), download_image=True)

    assert summary["total_urls"] == 4
    assert summary["succeeded"] == 1
    assert summary["failed"] == 3

    entries_by_url = {entry["url"]: entry for entry in summary["entries"]}

    # Every URL is present in the summary, in input order
    assert [entry["url"] for entry in summary["entries"]] == url_list

    invalid = entries_by_url[BAD_DOMAIN_URL]
    assert invalid["status"] == "failed"
    assert invalid["product_id"] is None
    assert invalid["output_dir"] is None
    assert "Invalid amazon product URL" in invalid["error"]

    net_error = entries_by_url[REQUEST_ERROR_URL]
    assert net_error["status"] == "failed"
    assert "network down" in net_error["error"]

    no_title = entries_by_url[MISSING_TITLE_URL]
    assert no_title["status"] == "failed"
    assert "title" in no_title["error"].lower()

    # The good URL still succeeds despite the earlier failures
    good = entries_by_url[PRODUCT_URL]
    assert good["status"] == "success"
    assert good["product_id"] == "B000GOOD01"
    good_dir = os.path.join(str(output_dir), "B000GOOD01")
    assert os.path.exists(os.path.join(good_dir, DEFAULT_OUTPUT_FILENAME))

    # Failed URLs leave no product directories behind
    assert sorted(os.listdir(str(output_dir))) == ["B000GOOD01", "batch-summary.json"]


def test_http_error_status_is_a_per_url_failure(monkeypatch, output_dir):
    pages = {
        "https://www.amazon.com/dp/B000HTTP404": FakeResponse(
            b"<html></html>", status_code=404
        )
    }
    install_stub_get(monkeypatch, pages)

    summary = process_url_batch(
        ["https://www.amazon.com/dp/B000HTTP404"], str(output_dir)
    )

    assert summary["succeeded"] == 0
    assert summary["failed"] == 1
    assert "404" in summary["entries"][0]["error"]
