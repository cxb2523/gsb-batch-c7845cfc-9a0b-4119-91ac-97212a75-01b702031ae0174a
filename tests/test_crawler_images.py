"""End-to-end crawl test with a local page and local image server.

Playwright renders the page for real, ``extract_content`` parses the real
HTML, ``download_images`` performs real HTTP requests and disk writes, and
``replace_image_links`` rewrites the real Markdown. Only the MLX model is
replaced by a deterministic test double.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mlx_mdx.config import CrawlConfig
from mlx_mdx.crawler import run_crawler
from mlx_mdx.models import ImageAsset, ImageOutcomeStatus

from .support import VALID_PNG, LocalImageServer, playwright_available


def _build_page_html(image_server: LocalImageServer) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Image Outcome Fixture Page</title>
    <meta name="description" content="A page used to validate the image pipeline.">
    <meta name="author" content="Test Author">
  </head>
  <body>
    <header><nav>site chrome that readability should ignore</nav></header>
    <main>
      <article>
        <h1>Image Outcome Fixture Page</h1>
        <p>This paragraph exists so readability keeps the article scope when it
        extracts the readable content. It is deliberately long enough to pass the
        minimum plain-text threshold used by the extraction helper, which needs at
        least two hundred characters before it accepts a candidate scope. We repeat
        that sentence with extra words to make sure the requirement is satisfied
        without relying on the images alone.</p>
        <p>More filler text follows here so the article body stays comfortably above
        the minimum length, and the images below are definitely part of the
        extracted summary rather than page chrome.</p>
        <figure>
          <img src="/good.png" alt="saved chart">
          <figcaption>A chart that downloads successfully.</figcaption>
        </figure>
        <p><img src="/missing.png" alt="missing figure"></p>
        <p><img src="/not-image.png" alt="not an image"></p>
        <p><img src="/too-small.png" alt="tiny image"></p>
        <p><img src="/too-large.png" alt="oversized image"></p>
        <p><img src="/slow.png" alt="slow image"></p>
        <p><img src="/good.png" alt="duplicate of saved chart"></p>
        <p><img src="data:image/png;base64,AAAA" alt="inline image"></p>
      </article>
    </main>
    <footer>footer chrome</footer>
  </body>
</html>
"""


class RecordingGenerator:
    """Deterministic stand-in for the MLX ReaderLM generator."""

    def __init__(self) -> None:
        self.requests: list = []
        self.base_url = ""

    def generate_body(self, metadata, content_html, plain_text, images):
        self.requests.append((metadata, content_html, plain_text, list(images)))
        saved_path = images[0].relative_path if images else "images/none.png"
        return (
            "## Captured body\n\n"
            f"![saved chart]({self.base_url}/good.png)\n\n"
            f"![missing figure]({self.base_url}/missing.png)\n\n"
            f"![Saved chart by path]({saved_path})\n\n"
            "![invented local](images/invented.png)\n"
        )

    def consume_model_load_seconds(self) -> float:
        return 0.0


def test_crawl_records_every_image_outcome_and_keeps_links_valid(tmp_path, monkeypatch):
    # Playwright also fetches these URLs while rendering the page, so keep the
    # oversized payload small and drive the limit from the module constant.
    monkeypatch.setattr("mlx_mdx.images.MAX_IMAGE_BYTES", 100_000)
    server = LocalImageServer()
    try:
        server.add("/page.html", body=b"", content_type="text/html")
        good_url = server.add("/good.png", body=VALID_PNG, content_type="image/png")
        server.add("/not-image.png", body=b"<html>nope</html>" * 64, content_type="text/html")
        server.add("/too-small.png", body=b"x" * 32, content_type="image/png")
        server.add(
            "/too-large.png",
            body=b"\x89PNG\r\n\x1a\n" + b"0" * 200_000,
            content_type="image/png",
        )
        server.add("/slow.png", body=VALID_PNG, content_type="image/png", delay=1.2)
        # /missing.png is intentionally absent -> 404.

        server.routes["/page.html"].body = _build_page_html(server).encode("utf-8")
        page_url = server.url("/page.html")

        generator = RecordingGenerator()
        generator.base_url = server.base_url

        config = CrawlConfig(
            output_root=tmp_path / "output",
            wait_after_load=0.0,
            navigation_timeout=15.0,
            max_html_chars=50_000,
            max_text_chars=20_000,
            image_timeout=0.4,
        )
        metrics = run_crawler_playwright([page_url], config, generator)
    finally:
        server.close()

    assert len(metrics) == 1
    metric = metrics[0]

    index_path = metric.output_path
    assert index_path.name == "index.md"
    assert index_path.parent.parent.parent == tmp_path / "output"
    assert index_path.parent.name == "image-outcome-fixture-page"
    images_dir = index_path.parent / "images"
    assert images_dir.is_dir()

    # ---- per-candidate outcomes are all present and readable ----
    outcomes = metric.image_outcomes
    assert len(outcomes) == 8
    by_alt = {outcome.alt_text: outcome for outcome in outcomes}
    assert by_alt["saved chart"].status is ImageOutcomeStatus.SAVED
    assert by_alt["duplicate of saved chart"].status is ImageOutcomeStatus.SAVED
    assert by_alt["missing figure"].status is ImageOutcomeStatus.SKIPPED_REQUEST_FAILED
    assert by_alt["not an image"].status is ImageOutcomeStatus.SKIPPED_UNSUPPORTED_TYPE
    assert by_alt["tiny image"].status is ImageOutcomeStatus.SKIPPED_TOO_SMALL
    assert by_alt["oversized image"].status is ImageOutcomeStatus.SKIPPED_TOO_LARGE
    assert by_alt["slow image"].status is ImageOutcomeStatus.SKIPPED_REQUEST_FAILED
    assert by_alt["inline image"].status is ImageOutcomeStatus.SKIPPED_DATA_URI
    for outcome in outcomes:
        if not outcome.saved:
            assert outcome.detail

    # Only one distinct file was written despite the duplicate URL.
    files = sorted(path.name for path in images_dir.iterdir())
    assert len(files) == 1

    saved_asset = metric.saved_images[0]
    assert saved_asset.absolute_url == good_url
    assert (index_path.parent / saved_asset.relative_path).read_bytes() == VALID_PNG
    assert by_alt["duplicate of saved chart"].relative_path == saved_asset.relative_path

    # ---- the manifest on disk mirrors the returned outcomes ----
    manifest_path = index_path.parent / "images.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_url"] == page_url
    assert manifest["totals"] == {
        "candidates": 8,
        "saved": 2,
        "skipped": 6,
        "files": 1,
    }
    assert [entry["status"] for entry in manifest["images"]] == [
        outcome.status.value for outcome in outcomes
    ]

    # ---- the Markdown only references files that exist ----
    markdown = index_path.read_text(encoding="utf-8")
    references = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
    assert references, "expected at least the saved image to be referenced"
    for reference in references:
        assert not reference.startswith(("http://", "https://", "data:"))
        assert (index_path.parent / reference).is_file(), reference
    assert references.count(saved_asset.relative_path) >= 1
    # No dangling reference to a candidate that failed to download.
    assert "missing.png" not in markdown
    assert "invented.png" not in markdown
    # Alt text for dropped images survives as plain text.
    assert "missing figure" in markdown

    # ---- the generator only saw saved assets, never skipped candidates ----
    _, _, _, images_seen = generator.requests[0]
    assert len(images_seen) == 1
    assert isinstance(images_seen[0], ImageAsset)
    assert images_seen[0].relative_path == saved_asset.relative_path


def run_crawler_playwright(urls, config, generator):
    """Run the crawler on a fresh event loop, skipping without a browser."""
    import asyncio

    if not playwright_available():
        pytest.skip("Playwright Chromium is not installed in this environment")
    return asyncio.run(run_crawler(urls, config, generator))
