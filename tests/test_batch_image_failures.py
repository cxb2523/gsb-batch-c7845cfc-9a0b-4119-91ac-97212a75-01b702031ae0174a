"""Partial image failure: one broken image must not abort the others."""

import json
import os

from amazon_product_details_scraper.config import DEFAULT_OUTPUT_FILENAME
from amazon_product_details_scraper.core.batch import process_url_batch

from conftest import (
    PRODUCT_URL,
    FakeResponse,
    build_product_html,
    install_failing_image_stub,
    install_stub_get,
)


IMAGE_URLS = [
    "https://m.media-amazon.com/images/good-1.jpg",
    "https://m.media-amazon.com/images/broken-2.jpg",
    "https://m.media-amazon.com/images/good-3.jpg",
]


def test_single_image_failure_keeps_remaining_images_and_details(
    monkeypatch, output_dir
):
    pages = {
        PRODUCT_URL: FakeResponse(
            build_product_html("B000IMG01", image_urls=IMAGE_URLS)
        )
    }
    install_stub_get(monkeypatch, pages)
    written = install_failing_image_stub(
        monkeypatch, broken_urls={"https://m.media-amazon.com/images/broken-2.jpg"}
    )

    summary = process_url_batch(
        [PRODUCT_URL], str(output_dir), download_image=True
    )

    # The URL itself still succeeds; only one image fails
    entry = summary["entries"][0]
    assert entry["status"] == "success"
    assert entry["images"]["succeeded"] == 2
    assert entry["images"]["failed"] == 1
    assert summary["images_succeeded"] == 2
    assert summary["images_failed"] == 1

    failure = entry["images"]["failures"][0]
    assert failure["url"] == "https://m.media-amazon.com/images/broken-2.jpg"
    assert failure["file_name"] == "image_2.jpg"
    assert "simulated image failure" in failure["reason"]

    product_dir = os.path.join(str(output_dir), "B000IMG01")

    # Already written details are kept
    with open(os.path.join(product_dir, DEFAULT_OUTPUT_FILENAME), encoding="utf8") as f:
        details = json.load(f)
    assert details["id"] == "B000IMG01"

    # Good images are on disk (1 and 3), the broken one is not; download 3
    # was attempted even though image 2 raised
    images_dir = os.path.join(product_dir, "images")
    assert sorted(os.listdir(images_dir)) == ["image_1.jpg", "image_3.jpg"]
    assert [item[2] for item in written] == ["image_1.jpg", "image_3.jpg"]


def test_all_images_broken_still_records_success_for_product(monkeypatch, output_dir):
    pages = {
        PRODUCT_URL: FakeResponse(
            build_product_html("B000IMG02", image_urls=IMAGE_URLS[:2])
        )
    }
    install_stub_get(monkeypatch, pages)
    install_failing_image_stub(
        monkeypatch, broken_urls=set(IMAGE_URLS[:2])
    )

    summary = process_url_batch(
        [PRODUCT_URL], str(output_dir), download_image=True
    )

    entry = summary["entries"][0]
    assert entry["status"] == "success"
    assert entry["images"]["succeeded"] == 0
    assert entry["images"]["failed"] == 2
    assert len(entry["images"]["failures"]) == 2

    product_dir = os.path.join(str(output_dir), "B000IMG02")
    # Details survive even when no image could be downloaded
    assert os.path.exists(os.path.join(product_dir, DEFAULT_OUTPUT_FILENAME))
    assert os.path.isdir(os.path.join(product_dir, "images"))
