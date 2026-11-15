"""
Main downloader with threading support for concurrent downloads.
"""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import random
import ssl
import threading
import time
from urllib.parse import urlparse
from typing import Optional, Callable
import requests
from rich.progress import Progress, TaskID, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn

from .models import MangaInfo, Chapter, DownloadConfig, OutputFormat
from ..formats.images import save_images, validate_image_bytes
from ..formats.pdf import create_pdf
from ..formats.cbz import create_cbz
from ..utils.logger import get_logger
from ..utils.session import get_session

logger = get_logger(__name__)

# Global event to signal cancellation across all downloaders
_cancel_event = threading.Event()

def cancel_downloads():
    """Signal all active downloaders to stop."""
    _cancel_event.set()
    logger.warning("Cancellation signal received. Stopping downloads...")

def reset_downloads():
    """Clear any previous cancellation signal before a new download run."""
    _cancel_event.clear()

def is_cancelled():
    """Check if cancellation has been signaled."""
    return _cancel_event.is_set()


@dataclass
class ImageDownloadReport:
    """Result of a multi-image download attempt."""

    images: list[tuple[int, bytes]]
    failed: list[tuple[int, str]]
    total: int
    failure_details: list["ImageFailure"] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return len(self.images) == self.total and not self.failed


@dataclass(frozen=True)
class ImageFailure:
    """Structured diagnostics for one failed page."""

    index: int
    category: str
    attempts: int
    message: str
    host: str = ""


class ImageDownloadPool:
    """Job-scoped executor with a small per-host connection gate."""

    def __init__(self, max_workers: int, per_host_limit: int = 4):
        self.max_workers = max(1, int(max_workers))
        self.per_host_limit = max(1, int(per_host_limit))
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._host_lock = threading.Lock()
        self._host_gates: dict[str, threading.BoundedSemaphore] = {}

    def host_gate(self, host: str) -> threading.BoundedSemaphore:
        key = host.lower() or "unknown"
        with self._host_lock:
            gate = self._host_gates.get(key)
            if gate is None:
                gate = threading.BoundedSemaphore(self.per_host_limit)
                self._host_gates[key] = gate
            return gate

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)


class ImageDownloader:
    """Downloads images with threading and retry logic."""
    
    def __init__(self, config: DownloadConfig, pool: ImageDownloadPool | None = None):
        self.config = config
        self.pool = pool
    
    def download_image(self, url: str, index: int) -> tuple[int, bytes | None, str | None]:
        """
        Download a single image with retry logic.
        
        Returns:
            Tuple of (index, image_bytes, error_message)
        """
        if url.startswith("data:image/"):
            try:
                import base64
                header, b64_data = url.split(",", 1)
                img_bytes = base64.b64decode(b64_data, validate=True)
                validate_image_bytes(img_bytes)
                return index, img_bytes, None
            except Exception as e:
                return index, None, f"Failed to decode or validate data URL: {e}"

        host = (urlparse(url).hostname or "").lower()
        last_error: Exception | None = None
        attempts = max(1, int(self.config.retry_count) + 1)
        for attempt in range(attempts):
            if is_cancelled():
                return index, None, "Download cancelled"
            try:
                gate = self.pool.host_gate(host) if self.pool else None
                if gate:
                    gate.acquire()
                try:
                    logger.debug("Starting download of image %s (attempt %s)", index, attempt + 1)
                    with get_session().get(
                        url,
                        timeout=(10, 30),
                        stream=True,
                        headers={"Connection": "close"} if attempt else None,
                    ) as response:
                        response.raise_for_status()
                        content = bytearray()
                        for chunk in response.iter_content(chunk_size=8192):
                            if is_cancelled():
                                raise InterruptedError("Download cancelled")
                            if chunk:
                                content.extend(chunk)
                        data = bytes(content)
                    validate_image_bytes(data)
                    return index, data, None
                finally:
                    if gate:
                        gate.release()
            except Exception as exc:
                last_error = exc
                if self._is_retryable(exc) and attempt + 1 < attempts:
                    get_session().reset()
                    delay = min(30.0, float(self.config.retry_delay) * (2 ** attempt))
                    delay += random.uniform(0, min(0.5, delay * 0.1))
                    logger.warning(
                        "Image %s failed (%s); retrying in %.1fs",
                        index,
                        self._category(exc),
                        delay,
                    )
                    time.sleep(delay)
                    continue
                break

        error = str(last_error) if last_error else "Download failed"
        logger.error("Image %s failed after %s attempts: %s", index, attempts, error)
        return index, None, error

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (InterruptedError, ValueError)):
            return False
        if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError)):
            return True
        if isinstance(exc, requests.exceptions.RequestException):
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            if status is not None:
                return status in {408, 425, 429, 500, 502, 503, 504}
            return isinstance(exc, (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.SSLError,
            ))
        return False

    @staticmethod
    def _category(exc: Exception) -> str:
        if isinstance(exc, ssl.SSLError):
            return "tls"
        if isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, requests.exceptions.SSLError):
            return "tls"
        if isinstance(exc, requests.exceptions.Timeout):
            return "timeout"
        if isinstance(exc, requests.exceptions.ConnectionError):
            return "connection"
        if isinstance(exc, requests.exceptions.HTTPError):
            return f"http_{getattr(exc.response, 'status_code', 'error')}"
        return type(exc).__name__.lower()

    def _download_indexed(
        self,
        indexed_urls: dict[int, str],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> ImageDownloadReport:
        own_pool = self.pool is None
        pool = self.pool or ImageDownloadPool(self.config.max_image_workers)
        previous_pool = self.pool
        if own_pool:
            self.pool = pool
        results: list[tuple[int, bytes]] = []
        failed: list[tuple[int, str]] = []
        details: list[ImageFailure] = []
        futures = {
            pool.executor.submit(self.download_image, url, index): index
            for index, url in indexed_urls.items()
        }
        try:
            for future in as_completed(futures):
                index = futures[future]
                try:
                    result_index, data, error = future.result()
                    if data is not None:
                        results.append((result_index, data))
                    else:
                        failed.append((result_index, error or "Download failed"))
                        details.append(ImageFailure(
                            result_index,
                            self._category_from_message(error or "Download failed"),
                            max(1, int(self.config.retry_count) + 1),
                            error or "Download failed",
                            (urlparse(indexed_urls[index]).hostname or "").lower(),
                        ))
                except Exception as exc:
                    failed.append((index, str(exc)))
                    details.append(ImageFailure(index, type(exc).__name__.lower(), 1, str(exc)))
                processed = len(results) + len(failed)
                if on_progress:
                    on_progress(processed, len(indexed_urls))
        finally:
            if own_pool:
                pool.shutdown()
                self.pool = previous_pool
        return ImageDownloadReport(
            images=sorted(results, key=lambda item: item[0]),
            failed=sorted(failed, key=lambda item: item[0]),
            total=len(indexed_urls),
            failure_details=sorted(details, key=lambda item: item.index),
        )

    @staticmethod
    def _category_from_message(message: str) -> str:
        lower = message.lower()
        if "ssl" in lower or "eof" in lower:
            return "tls"
        if "timeout" in lower:
            return "timeout"
        if "http 4" in lower or "http 5" in lower:
            return "http"
        return "network"
    
    def download_all_images_report(
        self,
        image_urls: list[str],
        progress: Optional[Progress] = None,
        task_id: Optional[TaskID] = None,
        on_progress: Optional[Callable[[int, int], None]] = None
    ) -> ImageDownloadReport:
        """
        Download all images concurrently.
        
        Returns:
            Report containing successful and failed image downloads
        """
        logger.info(f"Downloading {len(image_urls)} images concurrently...")
        callback = on_progress
        if progress and task_id:
            def callback(current, total):
                progress.advance(task_id)
                if on_progress:
                    on_progress(current, total)
        report = self._download_indexed(
            {index: url for index, url in enumerate(image_urls, 1)},
            callback,
        )
        if report.failed:
            logger.warning("%s images failed to download", len(report.failed))
        return report

    def download_indexed_images(
        self,
        indexed_urls: dict[int, str],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> ImageDownloadReport:
        """Retry a selected set of page indexes without redownloading others."""
        return self._download_indexed(indexed_urls, on_progress)

    def download_all_images(
        self,
        image_urls: list[str],
        progress: Optional[Progress] = None,
        task_id: Optional[TaskID] = None,
        on_progress: Optional[Callable[[int, int], None]] = None
    ) -> list[tuple[int, bytes]]:
        """Download all images and return only successful image bytes."""
        return self.download_all_images_report(
            image_urls, progress, task_id, on_progress
        ).images


def report_images_by_page(image_report) -> dict[int, str]:
    """Map each extracted image URL to its original reader page number.

    ``page_numbers`` is positionally aligned with ``image_urls``; using the
    real reader page numbers (instead of list positions) keeps skipped ad or
    placeholder pages from shifting every subsequent page index.
    Reports produced before ``page_numbers`` existed fall back to 1-based
    ordinals so their positional contract is preserved.
    """
    image_urls = list(getattr(image_report, "image_urls", None) or [])
    page_numbers = list(getattr(image_report, "page_numbers", None) or [])
    if len(page_numbers) != len(image_urls):
        page_numbers = list(range(1, len(image_urls) + 1))
    return {
        page: url
        for page, url in zip(page_numbers, image_urls)
        if page not in (None, 0)
    }


def report_expected_pages(image_report) -> list[int]:
    """Return the reader page numbers that should yield an image.

    Skipped ad/placeholder pages are excluded so an extracted chapter with
    skipped pages still counts as complete without fabricating downloads.
    """
    page_count = int(getattr(image_report, "page_count", 0) or 0)
    skipped = set(getattr(image_report, "skipped_pages", None) or [])
    if page_count > 0:
        return [
            page
            for page in range(1, page_count + 1)
            if page not in skipped
        ]
    # Legacy reports carry no page metadata: trust what was extracted.
    return list(report_images_by_page(image_report).keys())


class ChapterDownloader:
    """Downloads a single chapter with all its images."""

    def __init__(
        self,
        config: DownloadConfig,
        manga: MangaInfo,
        reader_service=None,
        image_pool: ImageDownloadPool | None = None,
    ):
        self.config = config
        self.manga = manga
        self.reader_service = reader_service
        self.image_downloader = ImageDownloader(config, pool=image_pool)

    def _fetch_reader_report(self, chapter: Chapter, use_reader_service: bool):
        """Re-open the chapter reader to obtain fresh image sources."""
        from ..api.comix import ComixAPI

        if use_reader_service:
            return self.reader_service.fetch(
                chapter.chapter_id,
                self.manga.slug or self.manga.hash_id,
                chapter.number,
            )
        return ComixAPI.get_chapter_image_report(
            chapter.chapter_id,
            manga_slug=self.manga.slug or self.manga.hash_id,
            chapter_number=chapter.number,
            headless=self.config.headless,
        )
    
    def download_chapter(
        self,
        chapter: Chapter,
        progress: Optional[Progress] = None,
        parent_task: Optional[TaskID] = None,
        on_image_progress: Optional[Callable[[int, int], None]] = None
    ) -> tuple[bool, str]:
        """
        Download a chapter and save in configured format.
        
        Returns:
            Tuple of (success, message)
        """
        manga_folder = self.manga.get_safe_title()
        chapter_folder = chapter.get_safe_folder_name()
        base_path = Path(self.config.download_path) / manga_folder
        
        try:
            if is_cancelled():
                return False, "Download cancelled"

            # Fetch image URLs
            from ..api.comix import ComixAPI
            report_method = ComixAPI.get_chapter_image_report
            use_reader_service = (
                self.reader_service is not None
                and getattr(report_method, "__module__", "src.api.comix") == "src.api.comix"
            )
            if use_reader_service:
                image_report = self.reader_service.fetch(
                    chapter.chapter_id,
                    self.manga.slug or self.manga.hash_id,
                    chapter.number,
                )
            else:
                image_report = ComixAPI.get_chapter_image_report(
                    chapter.chapter_id,
                    manga_slug=self.manga.slug or self.manga.hash_id,
                    chapter_number=chapter.number,
                    headless=self.config.headless
                ) if hasattr(ComixAPI, "get_chapter_image_report") else None

            if image_report is None:
                image_urls = ComixAPI.get_chapter_images(
                    chapter.chapter_id,
                    manga_slug=self.manga.slug or self.manga.hash_id,
                    chapter_number=chapter.number,
                    headless=self.config.headless
                )
                from ..api.comix import ChapterImageFetchReport
                image_report = ChapterImageFetchReport(image_urls)

            # Key every image by its original reader page number so skipped
            # ad/placeholder pages cannot shift subsequent page indexes.
            indexed_urls = report_images_by_page(image_report)
            expected_pages = report_expected_pages(image_report)
            expected_total = len(expected_pages)
            present_pages = set(indexed_urls)
            # Pages whose render timed out during extraction: keep the partial
            # chapter instead of failing it outright, then refresh the reader
            # and backfill only these pages below.
            missing_pages = sorted(
                set(expected_pages) - present_pages
            )

            if not indexed_urls:
                return False, f"No images found for {chapter.get_display_name()}"

            if is_cancelled():
                return False, "Download cancelled"

            # Create task for image downloads
            task_id = None
            if progress:
                task_id = progress.add_task(
                    f"[cyan]  └─ {chapter.get_display_name()}",
                    total=expected_total
                )

            # Download only the pages extraction managed to produce.
            download_report = self.image_downloader.download_indexed_images(
                indexed_urls, on_progress=on_image_progress
            )

            # Image download failures join the extraction-time missing pages so
            # a single reader refresh can recover both kinds, and successfully
            # downloaded pages are never requested again.
            missing_pages.extend(
                page
                for page, _error in download_report.failed
                if page not in missing_pages
            )

            # Signed CDN URLs can expire between reader extraction and the
            # image pass, and render-time failures leave pages unextracted.
            # Refresh the reader source once and retry only the missing page
            # numbers, preserving every successful page and progress count.
            if missing_pages:
                recovered_by_index: dict[int, bytes] = {}
                try:
                    refreshed = self._fetch_reader_report(chapter, use_reader_service)
                    refreshed_by_page = report_images_by_page(refreshed)
                    replacements = {
                        page: refreshed_by_page[page]
                        for page in missing_pages
                        if page in refreshed_by_page
                    }
                    if replacements:
                        logger.info(
                            "Retrying %s missing page(s) with refreshed chapter sources",
                            len(replacements),
                        )
                        recovered = self.image_downloader.download_indexed_images(
                            replacements, on_progress=on_image_progress
                        )
                        recovered_by_index = dict(recovered.images)
                except Exception as recovery_error:
                    logger.warning("Failed-page source refresh unavailable: %s", recovery_error)

                existing_by_index = dict(download_report.images)
                existing_by_index.update(recovered_by_index)
                remaining_missing = sorted(
                    page for page in missing_pages
                    if page not in recovered_by_index
                )
                prior_details = {
                    detail.index: detail
                    for detail in download_report.failure_details
                }

                def _failure_detail(page: int) -> ImageFailure:
                    if page in prior_details:
                        return prior_details[page]
                    return ImageFailure(
                        index=page,
                        category="extraction",
                        attempts=1,
                        message=(
                            "Page was not rendered by the chapter reader "
                            "after refreshing its source"
                        ),
                    )

                failure_details = [_failure_detail(page) for page in remaining_missing]
                download_report = ImageDownloadReport(
                    images=sorted(existing_by_index.items()),
                    failed=[
                        (page, _failure_detail(page).message)
                        for page in remaining_missing
                    ],
                    total=expected_total,
                    failure_details=sorted(
                        failure_details, key=lambda item: item.index
                    ),
                )

            image_data = download_report.images

            if is_cancelled():
                return False, "Download cancelled"
            
            if not image_data:
                return False, f"Failed to download any images for {chapter.get_display_name()}"

            if not download_report.is_complete:
                if self.config.output_format == OutputFormat.IMAGES:
                    save_images(image_data, base_path, chapter_folder)
                failed_page_numbers = [page for page, _error in download_report.failed]
                failed_text = ""
                if failed_page_numbers:
                    shown = ", ".join(str(page) for page in failed_page_numbers[:20])
                    suffix = "…" if len(failed_page_numbers) > 20 else ""
                    failed_text = f"; failed pages: {shown}{suffix}"
                return False, (
                    f"Incomplete download for {chapter.get_display_name()} "
                    f"({len(image_data)}/{download_report.total} pages{failed_text})"
                )
            
            # Save in configured format
            if self.config.output_format == OutputFormat.IMAGES:
                save_images(image_data, base_path, chapter_folder)
                
            elif self.config.output_format == OutputFormat.PDF:
                if self.config.keep_images:
                    image_paths = save_images(image_data, base_path, chapter_folder)
                    pdf_path = base_path / f"{chapter_folder}.pdf"
                    create_pdf(image_paths, pdf_path, chapter.get_display_name())
                else:
                    from ..formats.pdf import create_pdf_from_bytes
                    pdf_path = base_path / f"{chapter_folder}.pdf"
                    create_pdf_from_bytes(image_data, pdf_path, chapter.get_display_name())
                    
            elif self.config.output_format == OutputFormat.CBZ:
                if self.config.keep_images:
                    image_paths = save_images(image_data, base_path, chapter_folder)
                    cbz_path = base_path / f"{chapter_folder}.cbz"
                    create_cbz(image_paths, cbz_path, self.manga, chapter)
                else:
                    from ..formats.cbz import create_cbz_from_bytes
                    cbz_path = base_path / f"{chapter_folder}.cbz"
                    create_cbz_from_bytes(image_data, cbz_path, self.manga, chapter)
            
            if progress and task_id:
                progress.update(task_id, completed=expected_total)
            
            return True, f"Downloaded {chapter.get_display_name()} ({len(image_data)} pages)"
            
        except Exception as e:
            logger.error(f"Error downloading chapter {chapter.number}: {e}")
            return False, f"Error: {str(e)}"


class MangaDownloader:
    """Main downloader orchestrating concurrent chapter downloads."""
    
    def __init__(self, config: DownloadConfig):
        self.config = config
    
    def download_chapters(
        self,
        manga: MangaInfo,
        chapters: list[Chapter],
        progress: Progress,
        on_chapter_complete: Optional[Callable[[Chapter, bool, str], None]] = None
    ) -> tuple[int, int]:
        """
        Download multiple chapters concurrently.
        
        Returns:
            Tuple of (successful_count, failed_count)
        """
        successful = 0
        failed = 0
        reset_downloads()
        
        from ..api.comix import ChapterReaderService

        reader_service = ChapterReaderService(self.config.headless)
        image_pool = ImageDownloadPool(self.config.max_image_workers)
        chapter_downloader = ChapterDownloader(
            self.config,
            manga,
            reader_service=reader_service,
            image_pool=image_pool,
        )
        
        # Create main progress task
        main_task = progress.add_task(
            f"[bold green]Downloading {manga.title}",
            total=len(chapters)
        )
        
        try:
            with ThreadPoolExecutor(max_workers=self.config.max_chapter_workers) as executor:
                futures = {
                    executor.submit(
                        chapter_downloader.download_chapter,
                        chapter,
                        progress,
                        main_task
                    ): chapter
                    for chapter in chapters
                }

                for future in as_completed(futures):
                    if is_cancelled():
                        break
                    chapter = futures[future]
                    try:
                        success, message = future.result()
                        if success:
                            successful += 1
                        else:
                            failed += 1

                        if on_chapter_complete:
                            on_chapter_complete(chapter, success, message)

                    except Exception as e:
                        failed += 1
                        logger.error("Exception downloading chapter %s: %s", chapter.number, e)
                        if on_chapter_complete:
                            on_chapter_complete(chapter, False, str(e))

                    progress.advance(main_task)
        finally:
            image_pool.shutdown()
            reader_service.close()
        
        return successful, failed
