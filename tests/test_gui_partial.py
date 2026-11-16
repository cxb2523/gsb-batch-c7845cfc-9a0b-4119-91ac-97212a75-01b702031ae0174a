"""GUI tests for partial-download reporting.

Uses an offscreen Qt platform (set in ``conftest.py``); no window is shown and
no network access occurs.
"""

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from conftest import make_chapter  # noqa: E402
from models import DownloadResult, DownloadStatus, FailedPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def frame(qapp, test_config):
    from gui.frames.download_frame import DownloadFrame

    widget = DownloadFrame()
    yield widget
    widget.deleteLater()


def _partial_result(chapter_number=2, missing=(3,)):
    return DownloadResult(
        success=False,
        chapter=make_chapter(chapter_number),
        output_path="/tmp/chapter",
        error=f"Incomplete: {len(missing)} images failed",
        images_downloaded=4 - len(missing),
        images_failed=len(missing),
        failed_pages=[FailedPage(p, f"{p:04d}.webp") for p in missing],
        total_images=4,
        status=DownloadStatus.PARTIAL.value,
    )


def _failed_result(chapter_number=3):
    return DownloadResult(
        success=False,
        chapter=make_chapter(chapter_number),
        output_path="/tmp/chapter",
        error="Failed to download any images",
        images_failed=4,
        total_images=4,
        status=DownloadStatus.FAILED.value,
    )


def _success_result(chapter_number=1):
    return DownloadResult(
        success=True,
        chapter=make_chapter(chapter_number),
        output_path="/tmp/chapter",
        images_downloaded=4,
        total_images=4,
        status=DownloadStatus.SUCCESS.value,
    )


def _log_text(frame):
    return frame.log_text.toPlainText()


def test_partial_chapter_is_logged_as_partial_not_success(frame):
    frame.on_chapter_complete(_partial_result(chapter_number=2, missing=(3,)))

    log = _log_text(frame)
    assert "partially" in log
    assert "page(s) 3" in log
    assert "✓" not in log, "partial chapters must not use the success marker"


def test_failed_chapter_is_logged_as_error(frame):
    frame.on_chapter_complete(_failed_result(chapter_number=3))

    log = _log_text(frame)
    assert "✗" in log
    assert "Failed to download any images" in log


def test_finished_shows_partial_counts_and_warning(frame, monkeypatch):
    captured = {}

    def fake_warning(parent, title, message):
        captured["title"] = title
        captured["message"] = message
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **k: pytest.fail("should not report full success")),
    )

    results = [_success_result(1), _partial_result(2, (1, 3)), _failed_result(3)]
    frame.on_download_finished(results)

    assert "1 downloaded" in frame.status_label.text()
    assert "1 partial" in frame.status_label.text()
    assert "1 failed" in frame.status_label.text()
    assert captured["title"] == "Download Incomplete"
    assert "missing page(s) 1, 3" in captured["message"]


def test_finished_all_success_shows_information(frame, monkeypatch):
    captured = {}

    def fake_info(parent, title, message):
        captured["title"] = title
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", staticmethod(fake_info))
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: pytest.fail("should not warn on full success")),
    )

    frame.on_download_finished([_success_result(1), _success_result(2)])

    assert captured["title"] == "Download Complete"
    assert "2 downloaded" in frame.status_label.text()
    assert "0 partial" in frame.status_label.text()
