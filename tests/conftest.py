"""Shared pytest fixtures for the downloader test-suite.

Tests never touch the real atsu.moe site: a local fake client supplies chapter
pages and image bytes, and the global config is replaced with a fast,
retry-delay-free instance backed by a temporary directory.
"""

import os
import sys
from pathlib import Path

import pytest

# Force Qt into headless mode before PyQt6 is imported by any test module.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config as config_module  # noqa: E402
from config import Config  # noqa: E402
from models import Chapter, MangaInfo, Page  # noqa: E402


FAKE_IMAGE_BYTES = b"\x89PNG\r\n\x1a\nfake-image-payload"


class FakeClient:
    """Local stand-in for :class:`api.client.AtsuClient`.

    ``fail_images`` never succeeds; ``transient_failures`` maps an image path to
    the number of initial attempts that should fail before it succeeds.
    """

    def __init__(self, pages_by_chapter=None):
        self.pages_by_chapter = pages_by_chapter or {}
        self.fail_images = set()
        self.transient_failures = {}
        self.image_calls = {}
        self.closed = False

    def get_chapter_pages(self, manga_id, chapter_id):
        return list(self.pages_by_chapter.get(chapter_id, []))

    def download_image(self, image_path):
        self.image_calls[image_path] = self.image_calls.get(image_path, 0) + 1

        remaining = self.transient_failures.get(image_path, 0)
        if remaining > 0:
            self.transient_failures[image_path] = remaining - 1
            raise RuntimeError(f"transient failure for {image_path}")

        if image_path in self.fail_images:
            raise RuntimeError(f"simulated failure for {image_path}")

        return FAKE_IMAGE_BYTES

    def close(self):
        self.closed = True


def make_pages(count, chapter_slug="ch"):
    """Build ``count`` pages whose image paths are unique per chapter."""
    return [
        Page(
            id=f"{chapter_slug}-p{number}",
            image=f"/static/{chapter_slug}/{number:04d}.webp",
            number=number,
        )
        for number in range(1, count + 1)
    ]


def make_chapter(number, chapter_id=None, title=None, page_count=3):
    chapter_id = chapter_id or f"chapter-{number}"
    return Chapter(
        id=chapter_id,
        title=title or f"Chapter {number}",
        number=float(number),
        index=int(number),
        page_count=page_count,
    )


def make_manga(title="Test Manga", chapters=None):
    return MangaInfo(
        id="manga-test",
        title=title,
        manga_type="Manga",
        chapters=chapters or [],
    )


@pytest.fixture
def test_config(tmp_path, monkeypatch):
    """Install a fast, isolated global config for each test."""
    cfg = Config(
        download_format="images",
        keep_images=True,
        download_path=str(tmp_path / "downloads"),
        concurrent_chapters=2,
        concurrent_images=2,
        max_retries=2,
        retry_delay=0,
        enable_logs=False,
    )
    monkeypatch.setattr(config_module, "_config", cfg)
    return cfg


@pytest.fixture
def fake_client():
    return FakeClient()
