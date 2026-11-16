"""Tests for the GUI download worker's partial-result reporting."""

import pytest

pytest.importorskip("PyQt6.QtCore")

from conftest import make_chapter, make_manga, make_pages  # noqa: E402
from downloader import DownloadManager, RETRY_MANIFEST_FILENAME  # noqa: E402
from models import DownloadStatus  # noqa: E402


def test_worker_emits_partial_results_and_manifest(
    test_config, fake_client, monkeypatch
):
    import gui.workers.download_worker as worker_module

    ch_ok = make_chapter(1, page_count=2)
    ch_partial = make_chapter(2, page_count=2)
    pages_ok = make_pages(2, "ch1")
    pages_partial = make_pages(2, "ch2")
    fake_client.pages_by_chapter = {
        ch_ok.id: pages_ok,
        ch_partial.id: pages_partial,
    }
    fake_client.fail_images = {pages_partial[1].image}

    monkeypatch.setattr(
        worker_module, "DownloadManager", lambda: DownloadManager(fake_client)
    )

    manga = make_manga(title="Worker Manga", chapters=[ch_ok, ch_partial])
    worker = worker_module.DownloadWorker(manga, [ch_ok, ch_partial])

    completed = []
    finished = []
    worker.chapter_complete.connect(completed.append)
    worker.finished.connect(finished.append)

    # Run synchronously; signals are queued to this thread's receiver.
    worker.run()

    assert len(completed) == 2
    assert len(finished) == 1
    results = finished[0]

    by_chapter = {result.chapter.id: result for result in results}
    assert by_chapter[ch_ok.id].status == DownloadStatus.SUCCESS.value
    assert by_chapter[ch_partial.id].status == DownloadStatus.PARTIAL.value
    assert by_chapter[ch_partial.id].failed_page_numbers == [2]

    manifest_path = worker.retry_manifest_path
    assert manifest_path is not None
    assert manifest_path.name == RETRY_MANIFEST_FILENAME
    assert manifest_path.exists()


def test_worker_no_manifest_when_all_complete(
    test_config, fake_client, monkeypatch
):
    import gui.workers.download_worker as worker_module

    chapter = make_chapter(1, page_count=2)
    fake_client.pages_by_chapter = {chapter.id: make_pages(2, "ch1")}

    monkeypatch.setattr(
        worker_module, "DownloadManager", lambda: DownloadManager(fake_client)
    )

    worker = worker_module.DownloadWorker(
        make_manga(chapters=[chapter]), [chapter]
    )
    finished = []
    worker.finished.connect(finished.append)

    worker.run()

    assert worker.retry_manifest_path is None
    assert finished[0][0].status == DownloadStatus.SUCCESS.value
