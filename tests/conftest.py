"""Shared test fixtures and stub helpers.

Everything here is fully local: no real Amazon page or image URL is ever
requested. The network layer (``requests.get`` and ``download_image``) is
replaced with in-memory stubs.
"""

import pytest
import requests

from amazon_product_details_scraper.core import scraper


PRODUCT_URL = "https://www.amazon.com/dp/B000TEST01"
DUPLICATE_URL_A = "https://www.amazon.com/dp/B000DUP01"
DUPLICATE_URL_B = "https://www.amazon.in/dp/B000DUP01?ref=different"
REQUEST_ERROR_URL = "https://www.amazon.in/dp/B000NET01"
MISSING_TITLE_URL = "https://www.amazon.com/dp/B000NOTITLE"
BAD_DOMAIN_URL = "https://example.com/not-an-amazon-page"


class FakeResponse:
    """Minimal stand-in for a ``requests.Response`` object."""

    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


def build_product_html(asin, title="Test Product Title", image_urls=None):
    """Builds a local HTML page shaped like the Amazon pages we parse."""
    image_urls = image_urls or []
    image_objects = ",".join(
        '{"hiRes":"%s","large":"placeholder"}' % image_url for image_url in image_urls
    )
    title_html = (
        f'<span id="productTitle">  {title}  </span>' if title is not None else ""
    )
    return f"""
    <html>
      <body>
        <input id="ASIN" value="{asin}">
        {title_html}
        <div id="feature-bullets"><ul><li>first bullet</li></ul></div>
        <div id="imageBlock_feature_div">
          <script>
            'imageBlock': 'ImageBlockATF',
            "colorImages": {{ "initial": [{image_objects}] }}
          </script>
        </div>
      </body>
    </html>
    """.encode("utf-8")


def install_stub_get(monkeypatch, pages):
    """Replaces ``requests.get`` with a URL-keyed stub.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        pages: Mapping of URL to either a :class:`FakeResponse` or an
            exception instance which the stub raises instead.
    """

    def stub_get(url, *args, **kwargs):
        result = pages[url]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(scraper.requests, "get", stub_get)


def install_failing_image_stub(monkeypatch, broken_urls):
    """Replaces ``download_image`` with a stub that writes marker files.

    URLs contained in ``broken_urls`` raise instead, simulating a single
    broken image. Returns a list recording every successful download as
    ``(url, output_dir, file_name)`` tuples.
    """
    written = []

    def stub_download_image(url, output_dir, file_name, overwrite=False):
        if url in broken_urls:
            raise requests.ConnectionError(f"simulated image failure: {url}")
        with open(f"{output_dir}/{file_name}", "wb") as f:
            f.write(b"fake-image-bytes")
        written.append((url, output_dir, file_name))

    monkeypatch.setattr(scraper, "download_image", stub_download_image)
    return written


@pytest.fixture
def output_dir(tmp_path):
    return tmp_path / "output"
