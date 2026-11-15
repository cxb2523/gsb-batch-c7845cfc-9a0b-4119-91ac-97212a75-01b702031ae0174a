"""Duplicate ASIN paths: the same product id must not overwrite earlier output."""

import json
import os

from amazon_product_details_scraper.config import DEFAULT_OUTPUT_FILENAME
from amazon_product_details_scraper.core.batch import process_url_batch

from conftest import (
    DUPLICATE_URL_A,
    DUPLICATE_URL_B,
    PRODUCT_URL,
    FakeResponse,
    build_product_html,
    install_stub_get,
)


def test_same_asin_twice_uses_separate_numbered_directories(monkeypatch, output_dir):
    pages = {
        DUPLICATE_URL_A: FakeResponse(
            build_product_html("B000DUP01", title="First listing")
        ),
        DUPLICATE_URL_B: FakeResponse(
            build_product_html("B000DUP01", title="Second listing")
        ),
        PRODUCT_URL: FakeResponse(
            build_product_html("B000OTHER1", title="Unrelated product")
        ),
    }
    install_stub_get(monkeypatch, pages)

    url_list = [DUPLICATE_URL_A, PRODUCT_URL, DUPLICATE_URL_B]
    summary = process_url_batch(url_list, str(output_dir), download_image=False)

    assert summary["succeeded"] == 3
    assert summary["failed"] == 0

    entries_by_url = {entry["url"]: entry for entry in summary["entries"]}

    first_dir = os.path.join(str(output_dir), "B000DUP01")
    second_dir = os.path.join(str(output_dir), "B000DUP01_2")
    other_dir = os.path.join(str(output_dir), "B000OTHER1")

    assert entries_by_url[DUPLICATE_URL_A]["output_dir"] == first_dir
    assert entries_by_url[DUPLICATE_URL_B]["output_dir"] == second_dir
    assert entries_by_url[PRODUCT_URL]["output_dir"] == other_dir

    # Nothing was overwritten: each directory holds its own details
    with open(os.path.join(first_dir, DEFAULT_OUTPUT_FILENAME), encoding="utf8") as f:
        first_details = json.load(f)
    with open(os.path.join(second_dir, DEFAULT_OUTPUT_FILENAME), encoding="utf8") as f:
        second_details = json.load(f)
    assert first_details["title"] == "First listing"
    assert second_details["title"] == "Second listing"

    # All three product directories plus the summary file exist
    assert sorted(os.listdir(str(output_dir))) == [
        "B000DUP01",
        "B000DUP01_2",
        "B000OTHER1",
        "batch-summary.json",
    ]

    # The summary makes the shared product id visible
    assert summary["duplicate_product_ids"] == {
        "B000DUP01": [DUPLICATE_URL_A, DUPLICATE_URL_B]
    }


def test_triple_asin_gets_incrementing_suffixes(monkeypatch, output_dir):
    url_a = "https://www.amazon.com/dp/B000TRIP1"
    url_b = "https://www.amazon.ca/dp/B000TRIP1"
    url_c = "https://www.amazon.de/dp/B000TRIP1"
    pages = {
        url: FakeResponse(build_product_html("B000TRIP1"))
        for url in (url_a, url_b, url_c)
    }
    install_stub_get(monkeypatch, pages)

    summary = process_url_batch([url_a, url_b, url_c], str(output_dir))

    dirs = [entry["output_dir"] for entry in summary["entries"]]
    assert dirs == [
        os.path.join(str(output_dir), "B000TRIP1"),
        os.path.join(str(output_dir), "B000TRIP1_2"),
        os.path.join(str(output_dir), "B000TRIP1_3"),
    ]
    assert summary["duplicate_product_ids"] == {
        "B000TRIP1": [url_a, url_b, url_c]
    }
