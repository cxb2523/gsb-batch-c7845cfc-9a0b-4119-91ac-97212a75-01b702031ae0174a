"""Happy path: a valid URL is scraped, JSON is written and images download."""

import json
import os

from amazon_product_details_scraper.config import (
    BATCH_SUMMARY_FILENAME,
    DEFAULT_OUTPUT_FILENAME,
)
from amazon_product_details_scraper.core.batch import process_url_batch

from conftest import PRODUCT_URL, FakeResponse, build_product_html, install_stub_get


IMAGE_URLS = [
    "https://m.media-amazon.com/images/1.jpg",
    "https://m.media-amazon.com/images/2.png",
]


def test_normal_url_succeeds_with_details_and_images(monkeypatch, output_dir):
    pages = {
        PRODUCT_URL: FakeResponse(
            build_product_html("B000TEST01", image_urls=IMAGE_URLS)
        )
    }
    install_stub_get(monkeypatch, pages)
    written = install_succeeding_images(monkeypatch)

    summary = process_url_batch(
        [PRODUCT_URL], str(output_dir), download_image=True
    )

    # Overall counts
    assert summary["total_urls"] == 1
    assert summary["succeeded"] == 1
    assert summary["failed"] == 0
    assert summary["images_succeeded"] == 2
    assert summary["images_failed"] == 0
    assert summary["duplicate_product_ids"] == {}

    entry = summary["entries"][0]
    assert entry["url"] == PRODUCT_URL
    assert entry["status"] == "success"
    assert entry["product_id"] == "B000TEST01"
    product_dir = os.path.join(str(output_dir), "B000TEST01")
    assert entry["output_dir"] == product_dir
    assert entry["error"] is None

    # product-info.json content
    with open(os.path.join(product_dir, DEFAULT_OUTPUT_FILENAME), encoding="utf8") as f:
        details = json.load(f)
    assert details["id"] == "B000TEST01"
    assert details["title"] == "Test Product Title"
    assert details["image_urls"] == IMAGE_URLS

    # Both images were requested and written into the images subdirectory
    assert len(written) == 2
    images_dir = os.path.join(product_dir, "images")
    assert sorted(os.listdir(images_dir)) == ["image_1.jpg", "image_2.png"]

    # The summary file itself was written into the output directory
    summary_path = os.path.join(str(output_dir), BATCH_SUMMARY_FILENAME)
    assert os.path.exists(summary_path)
    with open(summary_path, encoding="utf8") as f:
        persisted = json.load(f)
    assert persisted["entries"][0]["url"] == PRODUCT_URL


def install_succeeding_images(monkeypatch):
    written = []

    def stub_download_image(url, output_dir, file_name, overwrite=False):
        with open(os.path.join(output_dir, file_name), "wb") as f:
            f.write(b"fake-image-bytes")
        written.append((url, output_dir, file_name))

    import amazon_product_details_scraper.core.scraper as scraper

    monkeypatch.setattr(scraper, "download_image", stub_download_image)
    return written
