#!/usr/bin/env python

import hashlib
import io
import json
import re
import threading
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from http import HTTPStatus
from time import sleep
from typing import Any

import requests
from kiwixstorage import NotFoundError
from PIL import Image, UnidentifiedImageError
from resizeimage.imageexceptions import ImageSizeError
from zimscraperlib.download import stream_file
from zimscraperlib.filesystem import get_content_mimetype
from zimscraperlib.image.optimization import OptimizeWebpOptions, optimize_webp
from zimscraperlib.image.probing import format_for
from zimscraperlib.image.transformation import resize_image

from sotoki.constants import (
    FILES_DOWNLOAD_FAILURE_MINIMUM_FOR_CHECK,
    FILES_DOWNLOAD_FAILURE_TRESHOLD_PER_TEN_THOUSAND,
    FILES_DOWNLOAD_INITIAL_INTERVAL,
    FILES_DOWNLOAD_MAX_INTERVAL,
    FILES_DOWNLOAD_MIN_INTERVAL,
    FILES_DOWNLOAD_SLOW_DOWN_FACTOR,
    FILES_DOWNLOAD_SPEED_UP_AFTER,
    FILES_DOWNLOAD_SPEED_UP_FACTOR,
    HTTP_REQUEST_TIMEOUT,
    IMAGES_ENCODER_VERSION,
    MAX_FILE_DOWNLOAD_RETRIES,
    MAX_RECORDED_ABANDONED_URLS,
    POSTS_IMAGE_SIZE,
    USER_AGENT,
)
from sotoki.utils.database.files import File, FileDatabase
from sotoki.utils.html import parse_retry_after_header
from sotoki.utils.shared import context, logger, shared


class DownloadOutcome(StrEnum):
    """Final outcome of a single image download attempt lifecycle"""

    SUCCESS = "success"
    # definitively given up: retries exhausted, 404, undecodable or invalid image
    ABANDONED = "abandoned"
    # file was picked for download but never processed because of a global abort
    UNPROCESSED = "unprocessed"


@dataclass(kw_only=True)
class HostData:
    files_to_download: FileDatabase
    hostname: str | None = None
    last_request_date: datetime | None = None
    request_interval_milliseconds: float = FILES_DOWNLOAD_INITIAL_INTERVAL
    not_before_date: datetime | None = None
    download_success: int = 0
    download_abandoned: int = 0
    download_unprocessed: int = 0
    nb_requested: int = 0
    # files requested for this host but not settled yet (queued or in-flight)
    nb_pending: int = 0
    abandoned_urls: list[str] = field(default_factory=list)
    downloads_complete: bool = False
    requests_done_since_last_speed_change: int = 0


@dataclass(frozen=True, kw_only=True)
class HostSettlement:
    """Settled download results for a single host"""

    hostname: str
    nb_requested: int
    nb_success: int
    nb_abandoned: int
    nb_unprocessed: int
    abandoned_urls: list[str]


@dataclass(frozen=True, kw_only=True)
class Settlement:
    """Settled download results for the whole image processing step"""

    aborted: bool
    abort_reason: str | None
    nb_requested: int
    nb_success: int
    nb_abandoned: int
    nb_unprocessed: int
    hosts: dict[str, HostSettlement]


@dataclass(kw_only=True)
class FileToDownload(File):
    host_data: HostData
    hostname: str


class Imager:
    def __init__(self):
        # list of source URLs that we've processed and added to ZIM
        self.aborted = False
        self.abort_reason: str | None = None
        self.handled = set()
        self.nb_requested = 0
        self.nb_failed = 0
        self.nb_done = 0
        self.nb_unprocessed = 0
        self.filesDatabases: list[FileDatabase] = []
        self.hosts: dict[str, HostData] = {}
        # protects settlement counters and hosts registry from concurrent workers;
        # deliberately not shared.lock because defer() might be called while it is held
        self._stats_lock = threading.Lock()
        self._settled = False
        # last settlement computed, available for querying once processing is over
        self.settlement: Settlement | None = None

    def abort(self, reason: str = "aborted on request"):
        """request imager to cancel processing of futures"""
        if not self.aborted:
            logger.error(f"Image downloads aborted: {reason}")
        self.abort_reason = self.abort_reason or reason
        self.aborted = True

    def record_success(self, file: FileToDownload):
        """Record that file has been successfully added to the ZIM"""
        with self._stats_lock:
            file.host_data.download_success += 1
            file.host_data.nb_pending = max(0, file.host_data.nb_pending - 1)

    def record_abandoned(self, file: FileToDownload, reason: str = ""):
        """Record that file will never be added to the ZIM (too many retries, 404…)"""
        with self._stats_lock:
            file.host_data.download_abandoned += 1
            file.host_data.nb_pending = max(0, file.host_data.nb_pending - 1)
            if len(file.host_data.abandoned_urls) < MAX_RECORDED_ABANDONED_URLS:
                file.host_data.abandoned_urls.append(file.url)
            self.nb_failed += 1
        logger.debug(
            f"Abandoned image {file.url} from {file.hostname}"
            f"{f' ({reason})' if reason else ''}"
        )

    def requeue(self, file: FileToDownload):
        """Put back a file which failed but can still be retried

        The file is still pending settlement, so nb_pending is left untouched."""
        with self._stats_lock:
            file.host_data.files_to_download.push(file)

    def settle(self) -> Settlement:
        """Settle download results, including files left unprocessed after an abort

        Must be called once workers are done. Files which were never settled (still
        queued or in-flight when the imager aborted) are accounted for as unprocessed.
        Successfully added items are never touched, so they survive partial failures."""
        with self._stats_lock:
            if not self._settled:
                for host_data in self.hosts.values():
                    host_data.download_unprocessed = host_data.nb_pending
                    host_data.nb_pending = 0
                    self.nb_unprocessed += host_data.download_unprocessed
                    host_data.downloads_complete = True
                self._settled = True

            host_settlements = {
                hostname: HostSettlement(
                    hostname=hostname,
                    nb_requested=host_data.nb_requested,
                    nb_success=host_data.download_success,
                    nb_abandoned=host_data.download_abandoned,
                    nb_unprocessed=host_data.download_unprocessed,
                    abandoned_urls=list(host_data.abandoned_urls),
                )
                for hostname, host_data in self.hosts.items()
            }
            nb_requested = sum(host.nb_requested for host in host_settlements.values())
            nb_success = sum(host.nb_success for host in host_settlements.values())
            nb_abandoned = sum(host.nb_abandoned for host in host_settlements.values())
            self.settlement = Settlement(
                aborted=self.aborted,
                abort_reason=self.abort_reason,
                nb_requested=nb_requested,
                nb_success=nb_success,
                nb_abandoned=nb_abandoned,
                nb_unprocessed=self.nb_unprocessed,
                hosts=host_settlements,
            )
            return self.settlement

    def log_settlement(self, settlement: Settlement):
        """Log a readable summary of settled download results"""
        if settlement.aborted:
            logger.error(
                f"Image downloads aborted ({settlement.abort_reason}): "
                f"{settlement.nb_success} succeeded, "
                f"{settlement.nb_abandoned} abandoned, "
                f"{settlement.nb_unprocessed} unprocessed "
                f"out of {settlement.nb_requested} requested"
            )
        else:
            logger.info(
                f"Image downloads settled: {settlement.nb_success} succeeded, "
                f"{settlement.nb_abandoned} abandoned, "
                f"{settlement.nb_unprocessed} unprocessed "
                f"out of {settlement.nb_requested} requested"
            )
        # also emit a machine-readable line so that results can be queried from logs
        payload = json.dumps(self.settlement_as_dict(settlement))
        logger.info(f"Image downloads settlement: {payload}")
        for hostname, host in sorted(settlement.hosts.items()):
            if (
                host.nb_abandoned == 0
                and host.nb_unprocessed == 0
                and host.nb_success == host.nb_requested
            ):
                logger.debug(
                    f"- {hostname}: {host.nb_success}/{host.nb_requested} succeeded"
                )
                continue
            msg = (
                f"- {hostname}: {host.nb_success}/{host.nb_requested} succeeded, "
                f"{host.nb_abandoned} abandoned, {host.nb_unprocessed} unprocessed"
            )
            if host.nb_abandoned:
                msg += f" (abandoned: {', '.join(host.abandoned_urls)})"
                if host.nb_abandoned > len(host.abandoned_urls):
                    msg += f" and {host.nb_abandoned - len(host.abandoned_urls)} more"
            logger.warning(msg)

    def settlement_as_dict(self, settlement: Settlement) -> dict[str, Any]:
        """JSON-serializable representation of settled download results"""
        return {
            "aborted": settlement.aborted,
            "abort_reason": settlement.abort_reason,
            "requested": settlement.nb_requested,
            "success": settlement.nb_success,
            "abandoned": settlement.nb_abandoned,
            "unprocessed": settlement.nb_unprocessed,
            "hosts": {
                hostname: {
                    "requested": host.nb_requested,
                    "success": host.nb_success,
                    "abandoned": host.nb_abandoned,
                    "unprocessed": host.nb_unprocessed,
                    "abandoned_urls": host.abandoned_urls,
                }
                for hostname, host in settlement.hosts.items()
            },
        }

    def get_image_data(self, url: str, **resize_args: Any) -> tuple[io.BytesIO, str]:
        """Bytes stream of an optimized, resized WebP of the source image"""
        src, webp = io.BytesIO(), io.BytesIO()
        stream_file(url=url, byte_stream=src, headers={"User-Agent": USER_AGENT})
        if format_for(src, from_suffix=False) == "SVG":
            src.seek(0)
            return src, "image/svg+xml"
        # first resize then convert to webp and optimize, because conversion to webp
        # proved to consume lots of memory ; a smaller image obviously consumes less
        resize_args = resize_args or {}
        try:
            resize_image(
                src,
                **resize_args,
                allow_upscaling=False,
            )
        except ImageSizeError:
            # ignore issues about image being too small, this is not an issue but a
            # expected behavior (rather than querying for image size and resizing
            # only if needed on our own, we let the library do it better than we
            # would do)
            pass
        with Image.open(src) as img:
            img.save(webp, format="WEBP")
        return (
            optimize_webp(  # pyright: ignore[reportReturnType]
                src=webp,
                options=OptimizeWebpOptions(lossless=False, quality=60, method=6),
            ),
            "image/webp",
        )

    def get_s3_key_for(self, url: str) -> str:
        """S3 key to use for that url"""
        return re.sub(r"^(https?)://", r"\1/", url)

    def get_digest_for(self, url: str) -> str:
        """Unique identifier of that url"""
        return hashlib.md5(url.encode("UTF-8")).hexdigest()  # nosec # noqa: S324

    def get_version_ident_for(self, url: str) -> str | None:
        """~version~ of the URL data to use for comparisons. Built from headers"""
        try:
            resp = requests.head(
                url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            headers = resp.headers
        except Exception:
            logger.debug(f"Unable to HEAD {url}")
            _, headers = stream_file(
                url=url,
                headers={"User-Agent": USER_AGENT},
                byte_stream=io.BytesIO(),
                block_size=1,
                only_first_block=True,
            )
        for header in ("ETag", "Last-Modified", "Content-Length"):
            if headers.get(header):
                return headers.get(header)

        return "-1"

    def defer(
        self,
        url: str,
        path: str | None = None,
    ) -> str | None:
        """request full processing of url, returning in-zim path immediately"""

        # find actual URL should it be from a provider
        try:
            parsed_url = urllib.parse.urlparse(url)
        except Exception:
            logger.warning(f"Can't parse image URL `{url}`. Skipping")
            return

        if parsed_url.scheme not in ("http", "https"):
            logger.warning(
                f"Not supporting image URL `{parsed_url.geturl()}`. Skipping"
            )
            return

        if not parsed_url.hostname:
            logger.warning(
                f"Not supporting empty image hostname `{parsed_url.geturl()}`. Skipping"
            )
            return

        digest = self.get_digest_for(parsed_url.geturl())
        if path is None:
            path = f"images/{digest}"

        with self._stats_lock:
            # do not add same image to process twice
            if digest in self.handled:
                return path

            # record that we are processing this one
            self.handled.add(digest)

            self.nb_requested += 1

            if parsed_url.hostname not in self.hosts:
                files_to_download = FileDatabase(parsed_url.hostname)
                self.filesDatabases.append(files_to_download)
                files_to_download.flush()
                self.hosts[parsed_url.hostname] = HostData(
                    files_to_download=files_to_download,
                    hostname=parsed_url.hostname,
                )
            host_data = self.hosts[parsed_url.hostname]
            host_data.nb_requested += 1
            # pending until settled (either successful, abandoned, or unprocessed)
            host_data.nb_pending += 1
            host_data.downloads_complete = False
            host_data.files_to_download.push(
                File(url=parsed_url.geturl(), zim_path=path, download_attempts=0)
            )

        return path

    def once_done(self):
        """default callback for single image processing"""
        # logger.debug("Once DONE")
        with self._stats_lock:
            self.nb_done += 1
        shared.progresser.update(incr=1)

    def process_images(self):
        logger.info("Starting images download")
        for hostname, host_data in self.hosts.items():
            logger.debug(f"- {hostname}: {host_data.files_to_download.len()}")
        shared.progresser.start(
            shared.progresser.IMAGES_STEP, nb_total=self.nb_requested
        )
        shared.img_executor.start()
        for _ in range(shared.img_executor.nb_workers):
            shared.img_executor.submit(self.worker_loop)
        while not self.aborted and not self.all_hosts_done:
            sleep(2)
        # workers might still be finishing their current download; settlement itself is
        # computed once they are all done (see settle()) so that successes are not lost
        return self.aborted

    @property
    def all_hosts_done(self) -> bool:
        return all(host.downloads_complete for host in self.hosts.values())

    def _pick_next_file(self) -> FileToDownload | None:
        """Pick next file to download, if any is available right now

        Must be called with shared.lock held (Redis queue and host state access).
        Returns None when no file can be picked right now, either because all hosts are
        done or because they are all throttled / have their files still in-flight."""
        now = datetime.now(UTC)

        for hostname, host_data in self.hosts.items():
            # check for conditions which leads to ignore current host
            if (
                host_data.downloads_complete
                or (
                    host_data.not_before_date is not None
                    and host_data.not_before_date > now
                )
                or (
                    host_data.last_request_date is not None
                    and host_data.last_request_date
                    + timedelta(milliseconds=host_data.request_interval_milliseconds)
                    > now
                )
            ):
                continue

            # grab new file from Redis queue
            file_to_download = host_data.files_to_download.pop()
            if file_to_download is None:
                with self._stats_lock:
                    if host_data.nb_pending:
                        # remaining files of this host are still in-flight in other
                        # workers (or waiting for a retry); do not mark it complete
                        continue
                    host_data.downloads_complete = True
                logger.debug(f"{hostname} has completed all its downloads")
                continue

            # modify lastRequestDate immediately so that all workers are aware
            host_data.last_request_date = now
            return FileToDownload(
                url=file_to_download.url,
                zim_path=file_to_download.zim_path,
                download_attempts=file_to_download.download_attempts,
                host_data=host_data,
                hostname=hostname,
            )

        return None

    def worker_loop(self):
        try:
            while not self.aborted:
                with shared.lock:
                    next_file = self._pick_next_file()
                    all_done = self.all_hosts_done
                if next_file is None:
                    if all_done or self.aborted:
                        break
                    # no host has something to process yet (just to not burn CPU);
                    # done outside the lock so that in-flight workers can write to ZIM
                    sleep(0.01)
                    continue
                self.process_image(next_file)
        except Exception as exc:
            self.abort(f"unexpected error in image worker: {exc}")
            logger.exception(exc, exc_info=context.debug)

    def _check_failure_threshold(self) -> bool:
        """Whether the failure ratio has been exceeded, meaning we should stop

        Only definitively abandoned downloads are considered as failures: unprocessed
        files must not be counted, otherwise aborting would be self-reinforcing."""
        with self._stats_lock:
            nb_failed, nb_requested = self.nb_failed, self.nb_requested
        return (
            nb_failed > FILES_DOWNLOAD_FAILURE_MINIMUM_FOR_CHECK
            and nb_requested > 0
            and (nb_failed * 10000) / nb_requested
            > FILES_DOWNLOAD_FAILURE_TRESHOLD_PER_TEN_THOUSAND
        )

    def _abort_on_too_many_failures(self):
        """Abort the whole image processing if the failure ratio is exceeded"""
        if self._check_failure_threshold():
            with self._stats_lock:
                nb_failed, nb_requested = self.nb_failed, self.nb_requested
            self.abort(
                f"too many files failed to download: [{nb_failed}/{nb_requested}]"
            )

    def process_image(
        self,
        file: FileToDownload,
    ):
        """download image from url or S3 and add to Zim at path. Upload if req."""

        if self.aborted:
            return

        if self._check_failure_threshold():
            # do not process this file, and ask all workers to stop: this file will be
            # accounted for as unprocessed in the settlement
            self._abort_on_too_many_failures()
            return

        file.download_attempts += 1
        try:
            outcome = self.download_image(file)
            if outcome is DownloadOutcome.SUCCESS:
                file.host_data.requests_done_since_last_speed_change += 1
                if (
                    file.host_data.requests_done_since_last_speed_change
                    > FILES_DOWNLOAD_SPEED_UP_AFTER
                    and file.host_data.request_interval_milliseconds
                    > FILES_DOWNLOAD_MIN_INTERVAL
                ):
                    file.host_data.request_interval_milliseconds = max(
                        FILES_DOWNLOAD_MIN_INTERVAL,
                        file.host_data.request_interval_milliseconds
                        / FILES_DOWNLOAD_SPEED_UP_FACTOR,
                    )
                    file.host_data.requests_done_since_last_speed_change = 0
                    logger.debug(
                        f"Speeding up {file.hostname} to"
                        f" {file.host_data.request_interval_milliseconds}ms interval"
                        f" ({file.host_data.files_to_download.len()} files remaining"
                        " to download from host)"
                    )
                self.record_success(file)
            elif outcome is DownloadOutcome.UNPROCESSED:
                # aborted while downloading this file: it stays pending and will be
                # accounted for as unprocessed in the settlement
                logger.debug(f"Processing of {file.url} interrupted by global abort")
            else:
                self.record_abandoned(file, reason="undownloadable")
                self._abort_on_too_many_failures()
        except requests.RequestException as exc:
            if exc.response is not None:
                err_details = f"HTTP error code {exc.response.status_code}"
            else:
                err_details = str(exc)
            logger.debug(
                f"Error downloading file {file.url}, failed with '{err_details}'"
            )
            if file.download_attempts > MAX_FILE_DOWNLOAD_RETRIES:
                logger.warning(
                    f"Error downloading file {file.url}, too many attempts failed, last"
                    f" one failed with '{err_details}', skipping"
                )
                self.record_abandoned(file, reason="too many attempts")
                self._abort_on_too_many_failures()
                return
            if (
                exc.response is not None
                and exc.response.status_code == HTTPStatus.NOT_FOUND
            ):
                logger.warning(
                    f"Error downloading file {file.url}, received a 404, skipping"
                )
                self.record_abandoned(file, reason="HTTP 404")
                self._abort_on_too_many_failures()
                return

            if exc.response is not None:
                retry_after_header = exc.response.headers.get("Retry-After")
                # ignore Retry-After header of some hosts giving bad values
                if retry_after_header and file.hostname not in ["i.imgur.com"]:
                    retry_date = parse_retry_after_header(retry_after_header)
                    if retry_date:
                        file.host_data.not_before_date = retry_date
                        logger.debug(
                            f"Received a [Retry-After={retry_after_header}], pausing "
                            f"down {file.hostname} until {retry_date}"
                        )
                    else:
                        logger.debug(
                            f"Received a [Retry-After={retry_after_header}] from "
                            f"{file.hostname} but failed to interpret it"
                        )

                if exc.response.status_code in [
                    HTTPStatus.TOO_MANY_REQUESTS,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    HTTPStatus.FORBIDDEN,  # Many CDNs return 403 when blocking
                    524,
                ]:
                    file.host_data.request_interval_milliseconds = min(
                        FILES_DOWNLOAD_MAX_INTERVAL,
                        file.host_data.request_interval_milliseconds
                        * FILES_DOWNLOAD_SLOW_DOWN_FACTOR,
                    )
                    file.host_data.requests_done_since_last_speed_change = 0
                    logger.debug(
                        f"Received a {exc.response.status_code} HTTP status for "
                        f"{file.url}. Slowing down {file.hostname} to"
                        f" {file.host_data.request_interval_milliseconds}ms interval"
                        f" ({file.host_data.files_to_download.len()} files remaining"
                        " to download from host)"
                    )

            if self.aborted:
                # do not requeue when aborting: file stays pending and is accounted for
                # as unprocessed in the settlement
                return

            # put file back in the queue for another attempt
            self.requeue(file)
        except Exception as exc:
            logger.warning(
                f"Error downloading file {file.url} due to '{exc}' exception, skipping",
                exc_info=context.debug,
            )
            self.record_abandoned(file, reason=f"unexpected error: {exc}")
            self._abort_on_too_many_failures()

    def download_image(self, file: FileToDownload) -> DownloadOutcome:
        if self.aborted:
            return DownloadOutcome.UNPROCESSED

        # setup resizing based on request
        resize_args = {"width": POSTS_IMAGE_SIZE}

        parsed_url = urllib.parse.urlparse(file.url)

        # just download, optimize and add to ZIM if not using S3
        if not context.s3_url_with_credentials:
            image_content, image_mimetype = self.get_image_data(
                parsed_url.geturl(), **resize_args
            )
            with shared.lock:
                shared.creator.add_item_for(
                    path=file.zim_path,
                    content=image_content.getvalue(),
                    mimetype=image_mimetype,
                    is_front=False,
                )
            self.once_done()
            return DownloadOutcome.SUCCESS

        if not shared.s3_storage:
            raise Exception("S3 storage should already have been initialized")

        # we are using S3 cache
        ident = self.get_version_ident_for(parsed_url.geturl())
        if ident is None:
            logger.warning(f"Unable to query {parsed_url.geturl()}. Skipping")
            return DownloadOutcome.ABANDONED

        key = self.get_s3_key_for(parsed_url.geturl())
        meta = {"ident": ident, "encoder_version": str(IMAGES_ENCODER_VERSION)}

        download_failed = False  # useful to trigger reupload or not
        try:
            # Attempting download from S3
            image_content = io.BytesIO()
            shared.s3_storage.download_matching_fileobj(key, image_content, meta=meta)
            # send only first bytes to get content mimetype, this is sufficient
            image_content.seek(0)
            image_mimetype = get_content_mimetype(image_content.read(2048))
            image_content.seek(0)
        except NotFoundError:
            # don't have it, not a download error. we'll upload after processing
            pass
        except UnidentifiedImageError:
            # problem decoding image => S3 cache is probably corrupted => let's
            # download and cache new version in S3
            logger.warning(
                f"failed to identify image mimetype from cache key {key}",
                exc_info=context.debug,
            )
        except Exception as exc:
            logger.warning(
                f"failed to download {key} from cache: {exc}", exc_info=context.debug
            )
            download_failed = True
        else:
            with shared.lock:
                shared.creator.add_item_for(
                    path=file.zim_path,
                    content=image_content.getvalue(),
                    mimetype=image_mimetype,
                    is_front=False,
                )
            self.once_done()
            return DownloadOutcome.SUCCESS

        if not key or not meta:
            raise Exception("s3_key and s3_meta should have been populated")

        # we're using S3 but don't have it or failed to download
        image_content, image_mimetype = self.get_image_data(
            parsed_url.geturl(), **resize_args
        )

        with shared.lock:
            shared.creator.add_item_for(
                path=file.zim_path,
                content=image_content.getvalue(),
                mimetype=image_mimetype,
                is_front=False,
            )
        self.once_done()

        # only upload it if we didn't have it in cache
        if not download_failed:
            logger.debug(f"Uploading {parsed_url.geturl()} to S3::{key} with {meta}")
            try:
                shared.s3_storage.upload_fileobj(
                    fileobj=image_content, key=key, meta=meta
                )
            except Exception as exc:
                logger.error(f"{key} failed to upload to cache: {exc}")

        return DownloadOutcome.SUCCESS
