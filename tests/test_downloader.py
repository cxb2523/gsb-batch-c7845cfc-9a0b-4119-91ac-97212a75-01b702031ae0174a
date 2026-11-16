"""Tests for chapter/image downloads, partial failures and the retry manifest.

All tests use a local fake client (see ``conftest.py``) and never access the
network.
"""

import json
from pathlib import Path

import pytest

from conftest import make_chapter, make_manga, make_pages
from downloader import DownloadManager, RETRY_MANIFEST_FILENAME
from downloader.images import ImageDownloader
from models import (
    DownloadResult,
    DownloadStatus,
    FailedPage,
    summarize_results,
)


def _setup_manager(fake_client, pages_by_chapter):
    fake_client.pages_by_chapter = pages_by_chapter
    return DownloadManager(fake_client)


def test_all_images_succeed_is_full_success(test_config, fake_client):
    chapter = make_chapter(1, page_count=3)
    pages = make_pages(3, "ch1")
    manager = _setup_manager(fake_client, {chapter.id: pages})

    result = manager.download_chapter(
        make_manga(chapters=[chapter]), chapter, Path(test_config.download_path)
    )

    assert result.success is True
    assert result.status == DownloadStatus.SUCCESS.value
    assert result.is_complete is True
    assert result.is_partial is False
    assert result.images_downloaded == 3
    assert result.images_failed == 0
    assert result.failed_pages == []
    assert result.total_images == 3
    # Images really landed on disk with zero-padded names.
    chapter_dir = Path(result.output_path)
    assert sorted(p.name for p in chapter_dir.iterdir()) == [
        "0001.webp", "0002.webp", "0003.webp"
    ]


def test_some_images_fail_is_partial_not_success(test_config, fake_client):
    chapter = make_chapter(2, page_count=3)
    pages = make_pages(3, "ch2")
    # Page 2 exhausts its retries.
    fake_client.fail_images = {pages[1].image}
    manager = _setup_manager(fake_client, {chapter.id: pages})

    result = manager.download_chapter(
        make_manga(chapters=[chapter]), chapter, Path(test_config.download_path)
    )

    assert result.success is False, "partial chapter must not be marked successful"
    assert result.status == DownloadStatus.PARTIAL.value
    assert result.is_partial is True
    assert result.is_complete is False
    assert result.images_downloaded == 2
    assert result.images_failed == 1
    assert result.total_images == 3
    assert result.failed_page_numbers == [2]
    assert len(result.failed_pages) == 1
    assert result.failed_pages[0].filename == "0002.webp"
    assert "Failed to download" in result.failed_pages[0].error
    # Successful images are still kept, and the failed one is absent.
    chapter_dir = Path(result.output_path)
    assert {p.name for p in chapter_dir.iterdir()} == {"0001.webp", "0003.webp"}


def test_all_images_fail_is_failure_not_no_pages(test_config, fake_client):
    chapter = make_chapter(3, page_count=3)
    pages = make_pages(3, "ch3")
    fake_client.fail_images = {page.image for page in pages}
    manager = _setup_manager(fake_client, {chapter.id: pages})

    result = manager.download_chapter(
        make_manga(chapters=[chapter]), chapter, Path(test_config.download_path)
    )

    assert result.success is False
    assert result.status == DownloadStatus.FAILED.value
    assert result.status != DownloadStatus.NO_PAGES.value
    assert result.images_downloaded == 0
    assert result.images_failed == 3
    assert result.failed_page_numbers == [1, 2, 3]
    # Errors identify pages, not just a wall of URLs.
    assert len(result.failed_pages) == 3
    assert all(page.filename for page in result.failed_pages)


def test_chapter_with_no_pages_is_distinct_status(test_config, fake_client):
    chapter = make_chapter(4, page_count=0)
    manager = _setup_manager(fake_client, {chapter.id: []})

    result = manager.download_chapter(
        make_manga(chapters=[chapter]), chapter, Path(test_config.download_path)
    )

    assert result.success is False
    assert result.status == DownloadStatus.NO_PAGES.value
    assert result.images_failed == 0
    assert result.failed_pages == []


def test_retry_backoff_still_recovers_transient_failures(test_config, fake_client):
    page = make_pages(1, "ch5")[0]
    fake_client.transient_failures = {page.image: test_config.max_retries - 1}
    downloader = ImageDownloader(fake_client)

    summary = downloader.download_chapter_images(
        [page], Path(test_config.download_path) / "retry-chapter"
    )

    assert summary.successful == 1
    assert summary.failed == 0
    assert fake_client.image_calls[page.image] == test_config.max_retries


def test_batch_writes_retry_manifest_with_only_incomplete_chapters(
    test_config, fake_client
):
    ch_ok = make_chapter(1, page_count=2)
    ch_partial = make_chapter(2, page_count=3)
    ch_failed = make_chapter(3, page_count=2)

    pages_ok = make_pages(2, "ch1")
    pages_partial = make_pages(3, "ch2")
    pages_failed = make_pages(2, "ch3")

    fake_client.pages_by_chapter = {
        ch_ok.id: pages_ok,
        ch_partial.id: pages_partial,
        ch_failed.id: pages_failed,
    }
    fake_client.fail_images = {
        pages_partial[0].image,   # page 1 of chapter 2
        pages_partial[2].image,   # page 3 of chapter 2
        pages_failed[0].image,
        pages_failed[1].image,
    }

    manga = make_manga(title="Manifest Manga", chapters=[ch_ok, ch_partial, ch_failed])
    manager = DownloadManager(fake_client)

    results = manager.download_chapters(manga, [0, 1, 2])

    by_chapter = {r.chapter.id: r for r in results}
    assert by_chapter[ch_ok.id].status == DownloadStatus.SUCCESS.value
    assert by_chapter[ch_partial.id].status == DownloadStatus.PARTIAL.value
    assert by_chapter[ch_failed.id].status == DownloadStatus.FAILED.value

    manifest_file = Path(test_config.download_path) / RETRY_MANIFEST_FILENAME
    assert manifest_file.exists()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    assert manifest["manga_title"] == "Manifest Manga"
    assert manifest["manga_id"] == manga.id
    assert manifest["total_chapters"] == 3

    entries = {entry["chapter_number"]: entry for entry in manifest["retry_chapters"]}
    assert set(entries) == {"2", "3"}  # complete chapter 1 excluded
    assert entries["2"]["failed_pages"] == [1, 3]
    assert entries["2"]["failed_files"] == ["0001.webp", "0003.webp"]
    assert entries["2"]["status"] == DownloadStatus.PARTIAL.value
    assert entries["2"]["chapter_title"] == ch_partial.title
    assert entries["3"]["failed_pages"] == [1, 2]
    assert set(manifest["retry_chapter_numbers"]) == {"2", "3"}


def test_no_manifest_when_every_chapter_is_complete(test_config, fake_client):
    chapter = make_chapter(1, page_count=2)
    fake_client.pages_by_chapter = {chapter.id: make_pages(2, "ch1")}
    manga = make_manga(chapters=[chapter])

    DownloadManager(fake_client).download_chapters(manga, [0])

    assert not (
        Path(test_config.download_path) / RETRY_MANIFEST_FILENAME
    ).exists()


def test_successful_retry_run_removes_stale_manifest(
    test_config, fake_client
):
    chapter = make_chapter(1, page_count=2)
    pages = make_pages(2, "ch1")
    fake_client.pages_by_chapter = {chapter.id: pages}
    fake_client.fail_images = {pages[0].image}
    manga = make_manga(chapters=[chapter])
    manager = DownloadManager(fake_client)

    manager.download_chapters(manga, [0])
    manifest_file = Path(test_config.download_path) / RETRY_MANIFEST_FILENAME
    assert manifest_file.exists()

    # Second run succeeds fully: the retry list must not linger.
    fake_client.fail_images = set()
    manager.download_chapters(manga, [0])

    assert not manifest_file.exists()


def test_retrying_one_chapter_keeps_other_pending_entries(
    test_config, fake_client
):
    ch1 = make_chapter(1, page_count=2)
    ch2 = make_chapter(2, page_count=2)
    pages1 = make_pages(2, "ch1")
    pages2 = make_pages(2, "ch2")
    fake_client.pages_by_chapter = {ch1.id: pages1, ch2.id: pages2}
    fake_client.fail_images = {pages1[0].image, pages2[0].image}
    manga = make_manga(chapters=[ch1, ch2])
    manager = DownloadManager(fake_client)

    manager.download_chapters(manga, [0, 1])
    manifest_file = Path(test_config.download_path) / RETRY_MANIFEST_FILENAME
    assert json.loads(manifest_file.read_text())["retry_chapter_numbers"] == ["1", "2"]

    # Retry only chapter 1 and make it succeed.
    fake_client.fail_images = {pages2[0].image}
    manager.download_chapters(manga, [0])

    manifest = json.loads(manifest_file.read_text())
    assert manifest["retry_chapter_numbers"] == ["2"]


def test_manifest_write_failure_does_not_discard_results(
    test_config, fake_client, monkeypatch
):
    chapter = make_chapter(1, page_count=2)
    pages = make_pages(2, "ch1")
    fake_client.pages_by_chapter = {chapter.id: pages}
    fake_client.fail_images = {pages[0].image}
    manga = make_manga(chapters=[chapter])

    def exploding_write_text(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", exploding_write_text)

    results = DownloadManager(fake_client).download_chapters(manga, [0])

    assert len(results) == 1
    assert results[0].status == DownloadStatus.PARTIAL.value
    assert results[0].images_downloaded == 1
    # A failed manifest write must not break the download or fake success.
    assert not (Path(test_config.download_path) / RETRY_MANIFEST_FILENAME).exists()


def test_summarize_results_counts_partial_separately():
    chapter = make_chapter(1)
    results = [
        DownloadResult(True, chapter, "", status=DownloadStatus.SUCCESS.value),
        DownloadResult(
            False,
            chapter,
            "",
            error="Incomplete: 1/2 images failed",
            images_downloaded=1,
            images_failed=1,
            failed_pages=[FailedPage(2, "0002.webp")],
            total_images=2,
            status=DownloadStatus.PARTIAL.value,
        ),
        DownloadResult(
            False,
            chapter,
            "",
            error="Failed to download any images",
            status=DownloadStatus.FAILED.value,
        ),
        DownloadResult(
            False, chapter, "", error="No pages found", status=DownloadStatus.NO_PAGES.value
        ),
    ]

    summary = summarize_results(results)

    assert summary.total == 4
    assert summary.successful == 1
    assert summary.partial == 1
    assert summary.failed == 2
    assert summary.not_fully_successful == 3


def test_partial_result_status_description_identifies_pages():
    chapter = make_chapter(7, title="Chapter 7")
    result = DownloadResult(
        False,
        chapter,
        "/tmp/chapter 7",
        error="Incomplete: 2/5 images failed",
        images_downloaded=3,
        images_failed=2,
        failed_pages=[FailedPage(2, "0002.webp"), FailedPage(5, "0005.webp")],
        total_images=5,
        status=DownloadStatus.PARTIAL.value,
    )

    description = result.describe_status()

    assert "partially" in description
    assert "3/5" in description
    assert "2, 5" in description
