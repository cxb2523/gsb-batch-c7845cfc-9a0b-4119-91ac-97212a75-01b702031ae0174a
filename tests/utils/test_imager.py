"""Tests for the image download settlement logic

Images are downloaded per-host with throttling; those tests exercise the settlement
of download results (success / definitively abandoned / unprocessed after abort)
without needing Redis nor network."""

import json
import logging
from http import HTTPStatus
from io import BytesIO
from unittest.mock import MagicMock

import pytest
import requests

from sotoki.constants import (
    FILES_DOWNLOAD_FAILURE_MINIMUM_FOR_CHECK,
    FILES_DOWNLOAD_FAILURE_TRESHOLD_PER_TEN_THOUSAND,
    FILES_DOWNLOAD_INITIAL_INTERVAL,
    MAX_FILE_DOWNLOAD_RETRIES,
)
from sotoki.utils import imager as imager_module
from sotoki.utils.executor import SotokiExecutor
from sotoki.utils.imager import DownloadOutcome, HostSettlement, Imager, Settlement
from sotoki.utils.shared import shared


class FakeRedisDatabase:
    """Minimal in-memory stand-in for the Redis-backed FileDatabase lists"""

    def __init__(self):
        self.lists: dict[str, list[bytes]] = {}

    def safe_command(self, command: str, *args):
        name = args[0]
        if command == "lpush":
            self.lists.setdefault(name, []).insert(0, args[1])
        elif command == "rpop":
            queue = self.lists.get(name) or []
            return queue.pop() if queue else None
        elif command == "llen":
            return len(self.lists.get(name) or [])
        elif command == "delete":
            self.lists.pop(name, None)
        else:  # pragma: no cover
            raise NotImplementedError(command)
        return None


@pytest.fixture
def creator(monkeypatch):
    creator = MagicMock()
    monkeypatch.setattr(shared, "creator", creator, raising=False)
    monkeypatch.setattr(shared, "progresser", MagicMock(), raising=False)
    return creator


@pytest.fixture
def imager(monkeypatch, creator):
    # creator fixture installs the shared creator/progresser needed by the imager
    assert creator
    monkeypatch.setattr(shared, "database", FakeRedisDatabase(), raising=False)
    return Imager()


def http_error(status_code: int, retry_after: str | None = None):
    response = requests.Response()
    response.status_code = status_code
    if retry_after:
        response.headers["Retry-After"] = retry_after
    return requests.HTTPError(response=response)


def stub_image_data(behavior):
    """Build a get_image_data replacement dispatching on url

    behavior(url) should either return (stream, mimetype) or raise."""

    def get_image_data(_self, url, **_kwargs):
        return behavior(url)

    return get_image_data


def always_succeed(_url):
    return BytesIO(b"image"), "image/webp"


def failing(*, status_code: int, retry_after: str | None = None):
    def behavior(_url):
        raise http_error(status_code, retry_after)

    return behavior


def run_pending(imager: Imager, max_steps: int = 10000) -> int:
    """Process queued files one by one, ignoring throttling waits"""
    steps = 0
    while not imager.aborted and steps < max_steps:
        with shared.lock:
            next_file = imager._pick_next_file()
        if next_file is None:
            break
        imager.process_image(next_file)
        for host_data in imager.hosts.values():
            host_data.last_request_date = None
            host_data.not_before_date = None
        steps += 1
    assert steps < max_steps, "image processing did not converge"
    return steps


def zim_paths(creator: MagicMock) -> set[str]:
    return {call.kwargs["path"] for call in creator.add_item_for.call_args_list}


class TestSettlementNormal:
    def test_all_hosts_succeed(self, imager, creator, monkeypatch):
        monkeypatch.setattr(Imager, "get_image_data", stub_image_data(always_succeed))

        paths = [
            imager.defer("https://a.example.com/1.png"),
            imager.defer("https://a.example.com/2.png"),
            imager.defer("https://b.example.com/3.png"),
        ]
        run_pending(imager)

        settlement = imager.settle()
        assert settlement.aborted is False
        assert settlement.abort_reason is None
        assert settlement.nb_requested == 3
        assert settlement.nb_success == 3
        assert settlement.nb_abandoned == 0
        assert settlement.nb_unprocessed == 0
        assert set(settlement.hosts) == {"a.example.com", "b.example.com"}
        assert settlement.hosts["a.example.com"].nb_success == 2
        assert settlement.hosts["b.example.com"].nb_success == 1
        assert zim_paths(creator) == set(paths)
        # paths inside the ZIM are unchanged
        assert all(path.startswith("images/") for path in paths)

    def test_settle_is_idempotent_and_serializable(self, imager, creator, monkeypatch):
        monkeypatch.setattr(Imager, "get_image_data", stub_image_data(always_succeed))
        imager.defer("https://a.example.com/1.png")
        run_pending(imager)

        first = imager.settle()
        second = imager.settle()
        assert first == second
        payload = imager.settlement_as_dict(second)
        assert json.loads(json.dumps(payload)) == payload
        assert payload["success"] == 1
        assert payload["hosts"]["a.example.com"]["success"] == 1
        assert zim_paths(creator) == {
            "images/" + Imager().get_digest_for("https://a.example.com/1.png")
        }


class TestAbandonedDownloads:
    def test_host_with_persistent_429_is_abandoned(self, imager, creator, monkeypatch):
        throttled_url = "https://throttled.example.com/1.png"

        def behavior(url):
            if url == throttled_url:
                return failing(status_code=HTTPStatus.TOO_MANY_REQUESTS)(url)
            return always_succeed(url)

        monkeypatch.setattr(Imager, "get_image_data", stub_image_data(behavior))

        imager.defer(throttled_url)
        imager.defer("https://healthy.example.com/2.png")
        imager.defer("https://healthy.example.com/3.png")
        throttled = imager.hosts["throttled.example.com"]
        # start from the default interval so that slowdown is observable
        throttled.request_interval_milliseconds = FILES_DOWNLOAD_INITIAL_INTERVAL

        run_pending(imager)

        settlement = imager.settle()
        assert settlement.aborted is False
        assert settlement.nb_abandoned == 1
        assert settlement.nb_unprocessed == 0
        assert settlement.hosts["throttled.example.com"].nb_abandoned == 1
        assert settlement.hosts["throttled.example.com"].nb_success == 0
        assert settlement.hosts["throttled.example.com"].abandoned_urls == [
            throttled_url
        ]
        # one host failing does not prevent other hosts from being downloaded
        assert settlement.hosts["healthy.example.com"].nb_success == 2
        assert len(zim_paths(creator)) == 2
        # throttling logic is preserved: host interval has been slowed down
        assert throttled.request_interval_milliseconds > FILES_DOWNLOAD_INITIAL_INTERVAL

    def test_retry_after_is_honored(self, imager, monkeypatch):
        responses = [http_error(HTTPStatus.TOO_MANY_REQUESTS, retry_after="120")]

        def behavior(_url):
            if responses:
                raise responses.pop()
            return BytesIO(b"image"), "image/webp"

        monkeypatch.setattr(Imager, "get_image_data", stub_image_data(behavior))
        imager.defer("https://a.example.com/1.png")
        host = imager.hosts["a.example.com"]

        with shared.lock:
            next_file = imager._pick_next_file()
        imager.process_image(next_file)

        assert host.not_before_date is not None
        assert host.download_abandoned == 0

    def test_bad_image_is_skipped_others_continue(self, imager, creator, monkeypatch):
        def behavior(url):
            if url.endswith("broken.png"):
                raise ValueError("cannot identify image file")
            return always_succeed(url)

        monkeypatch.setattr(Imager, "get_image_data", stub_image_data(behavior))

        imager.defer("https://bad.example.com/broken.png")
        imager.defer("https://good.example.com/1.png")
        imager.defer("https://good.example.com/2.png")
        run_pending(imager)

        settlement = imager.settle()
        assert settlement.aborted is False
        assert settlement.nb_abandoned == 1
        assert settlement.nb_unprocessed == 0
        assert settlement.hosts["bad.example.com"].abandoned_urls == [
            "https://bad.example.com/broken.png"
        ]
        assert settlement.hosts["good.example.com"].nb_success == 2
        assert len(zim_paths(creator)) == 2

    def test_404_is_abandoned_without_retry(self, imager, monkeypatch):
        calls = []

        def behavior(url):
            calls.append(url)
            return failing(status_code=HTTPStatus.NOT_FOUND)(url)

        monkeypatch.setattr(Imager, "get_image_data", stub_image_data(behavior))
        imager.defer("https://a.example.com/1.png")
        run_pending(imager)

        settlement = imager.settle()
        assert settlement.nb_abandoned == 1
        assert settlement.nb_unprocessed == 0
        assert len(calls) == 1


class TestAbortSettlement:
    def test_abort_marks_unprocessed(self, imager, creator, monkeypatch):
        monkeypatch.setattr(Imager, "get_image_data", stub_image_data(always_succeed))
        for index in range(4):
            imager.defer(f"https://a.example.com/{index}.png")

        # process one image, then abort as the scraper would on error
        with shared.lock:
            next_file = imager._pick_next_file()
        imager.process_image(next_file)
        imager.abort("simulated scraper error")

        settlement = imager.settle()
        assert settlement.aborted is True
        assert settlement.abort_reason == "simulated scraper error"
        assert settlement.nb_success == 1
        assert settlement.nb_abandoned == 0
        assert settlement.nb_unprocessed == 3
        assert settlement.hosts["a.example.com"].nb_unprocessed == 3
        # successfully added image is kept
        assert len(zim_paths(creator)) == 1

    def test_failure_ratio_aborts_and_counts_unprocessed(
        self, imager, creator, monkeypatch
    ):
        # use a small threshold configuration so that the test stays tiny
        monkeypatch.setattr(
            imager_module, "FILES_DOWNLOAD_FAILURE_MINIMUM_FOR_CHECK", 2
        )
        monkeypatch.setattr(
            imager_module, "FILES_DOWNLOAD_FAILURE_TRESHOLD_PER_TEN_THOUSAND", 1000
        )

        def behavior(url):
            if "broken" in url:
                return failing(status_code=HTTPStatus.NOT_FOUND)(url)
            return always_succeed(url)

        monkeypatch.setattr(Imager, "get_image_data", stub_image_data(behavior))

        # bad host first so that failures are settled before healthy files are reached
        for index in range(4):
            imager.defer(f"https://bad.example.com/broken-{index}.png")
        for index in range(20):
            imager.defer(f"https://good.example.com/{index}.png")

        run_pending(imager)
        settlement = imager.settle()

        assert settlement.aborted is True
        assert settlement.abort_reason is not None
        assert "too many files failed" in settlement.abort_reason
        assert settlement.nb_abandoned == 3
        # abort is triggered on the 3rd failure (strictly above the 2 failures
        # minimum) and everything else is reported as unprocessed, not as failed
        assert settlement.nb_unprocessed > 0
        assert (
            settlement.nb_success + settlement.nb_abandoned + settlement.nb_unprocessed
            == settlement.nb_requested
        )
        # images which made it into the ZIM before the abort are kept
        assert settlement.nb_success == len(zim_paths(creator))

    def test_single_host_failure_does_not_abort_others(self, imager, monkeypatch):
        monkeypatch.setattr(
            imager_module, "FILES_DOWNLOAD_FAILURE_MINIMUM_FOR_CHECK", 50
        )
        monkeypatch.setattr(
            imager_module, "FILES_DOWNLOAD_FAILURE_TRESHOLD_PER_TEN_THOUSAND", 1000
        )

        def behavior(url):
            if "broken" in url:
                return failing(status_code=HTTPStatus.NOT_FOUND)(url)
            return always_succeed(url)

        monkeypatch.setattr(Imager, "get_image_data", stub_image_data(behavior))

        # 4 failures on a single host is below the minimum threshold and below the ratio
        for index in range(4):
            imager.defer(f"https://bad.example.com/broken-{index}.png")
        for index in range(100):
            imager.defer(f"https://good.example.com/{index}.png")

        run_pending(imager)
        settlement = imager.settle()

        assert settlement.aborted is False
        assert settlement.nb_abandoned == 4
        assert settlement.nb_unprocessed == 0
        assert settlement.hosts["good.example.com"].nb_success == 100

    def test_abort_does_not_requeue_inflight_file(self, imager, monkeypatch):
        """A retryable error received right after an abort must not loop forever"""
        monkeypatch.setattr(
            Imager,
            "get_image_data",
            stub_image_data(failing(status_code=HTTPStatus.SERVICE_UNAVAILABLE)),
        )
        imager.defer("https://a.example.com/1.png")
        host = imager.hosts["a.example.com"]

        with shared.lock:
            next_file = imager._pick_next_file()
        imager.abort("simulated scraper error")
        imager.process_image(next_file)

        assert host.files_to_download.len() == 0
        settlement = imager.settle()
        assert settlement.nb_unprocessed == 1
        assert settlement.nb_abandoned == 0


class TestOutcome:
    def test_retries_respect_max_attempts(self, imager, monkeypatch):
        calls = []

        def behavior(url):
            calls.append(url)
            return failing(status_code=HTTPStatus.SERVICE_UNAVAILABLE)(url)

        monkeypatch.setattr(Imager, "get_image_data", stub_image_data(behavior))
        imager.defer("https://a.example.com/1.png")
        run_pending(imager)

        settlement = imager.settle()
        assert settlement.nb_abandoned == 1
        assert len(calls) == MAX_FILE_DOWNLOAD_RETRIES + 1

    def test_download_outcome_values(self):
        assert DownloadOutcome.SUCCESS.value == "success"
        assert DownloadOutcome.ABANDONED.value == "abandoned"
        assert DownloadOutcome.UNPROCESSED.value == "unprocessed"

    def test_log_settlement_reports_hosts(self, imager):
        settlement = Settlement(
            aborted=True,
            abort_reason="too many files failed",
            nb_requested=10,
            nb_success=4,
            nb_abandoned=2,
            nb_unprocessed=4,
            hosts={
                "a.example.com": HostSettlement(
                    hostname="a.example.com",
                    nb_requested=10,
                    nb_success=4,
                    nb_abandoned=2,
                    nb_unprocessed=4,
                    abandoned_urls=[
                        "https://a.example.com/1.png",
                        "https://a.example.com/2.png",
                    ],
                )
            },
        )
        messages = []

        class Collector(logging.Handler):
            def emit(self, record):
                messages.append(record.getMessage())

        collector = Collector()
        imager_module.logger.addHandler(collector)
        try:
            imager.log_settlement(settlement)
        finally:
            imager_module.logger.removeHandler(collector)

        output = "\n".join(messages)
        assert "unprocessed" in output
        assert "too many files failed" in output
        assert "a.example.com" in output
        assert "https://a.example.com/1.png" in output


def test_threshold_constants_unchanged():
    """The existing failure threshold must be kept as-is"""
    assert FILES_DOWNLOAD_FAILURE_MINIMUM_FOR_CHECK == 50
    assert FILES_DOWNLOAD_FAILURE_TRESHOLD_PER_TEN_THOUSAND == 1000


class TestThreadedProcessing:
    """Exercise the real threaded scheduling loop (process_images + workers)"""

    def test_process_images_reaches_settlement(self, imager, creator, monkeypatch):
        monkeypatch.setattr(Imager, "get_image_data", stub_image_data(always_succeed))
        # no throttling so that the test finishes fast
        monkeypatch.setattr(imager_module, "FILES_DOWNLOAD_INITIAL_INTERVAL", 0)

        for index in range(5):
            imager.defer(f"https://a.example.com/{index}.png")
        for index in range(3):
            imager.defer(f"https://b.example.com/{index}.png")

        executor = SotokiExecutor(queue_size=10, nb_workers=3, prefix="IMG-TEST-")
        monkeypatch.setattr(shared, "img_executor", executor, raising=False)
        monkeypatch.setattr(imager_module, "sleep", lambda _seconds: None)
        try:
            imager.process_images()
        finally:
            executor.join()
            executor.shutdown()

        settlement = imager.settle()
        assert settlement.nb_requested == 8
        assert settlement.nb_abandoned == 0
        assert settlement.nb_unprocessed == 0
        assert settlement.nb_success == 8
        assert len(zim_paths(creator)) == 8

    def test_settle_without_processing_marks_all_unprocessed(self, imager, creator):
        imager.defer("https://a.example.com/1.png")
        imager.defer("https://b.example.com/2.png")
        imager.abort("aborted before any worker started")

        settlement = imager.settle()
        assert settlement.nb_unprocessed == 2
        assert settlement.nb_success == 0
        assert settlement.nb_abandoned == 0
        assert zim_paths(creator) == set()
