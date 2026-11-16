"""Image downloader with concurrent downloads and retry logic."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Tuple

from api.client import AtsuClient
from config import get_config
from models import FailedPage, Page

logger = logging.getLogger(__name__)


@dataclass
class ImageDownloadSummary:
    """Detailed outcome of downloading all images of a chapter.

    Alongside the successful count, this records every failed page with its
    page number, generated filename and error message so callers can report
    exactly which images are missing.
    """

    total: int = 0
    successful: int = 0
    failed_pages: List[FailedPage] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def failed(self) -> int:
        """Number of images that could not be downloaded."""
        return len(self.failed_pages)

    @property
    def all_succeeded(self) -> bool:
        """True when at least one image was requested and all succeeded."""
        return self.total > 0 and self.failed == 0

    @property
    def none_succeeded(self) -> bool:
        """True when images were requested but none could be downloaded."""
        return self.total > 0 and self.successful == 0

    def __iter__(self) -> Iterator[object]:
        """Allow legacy ``(successful, errors)`` tuple unpacking."""
        yield self.successful
        yield self.errors


class ImageDownloader:
    """Downloads chapter images with concurrency and retry support."""

    def __init__(self, client: Optional[AtsuClient] = None):
        self.client = client or AtsuClient()
        self._owns_client = client is None

    def download_image_with_retry(
        self,
        page: Page,
        output_path: Path
    ) -> Tuple[bool, str]:
        """Download a single image with retry logic.

        Returns:
            Tuple of (success, error_message)
        """
        config = get_config()
        max_retries = config.max_retries
        base_delay = config.retry_delay

        for attempt in range(max_retries):
            try:
                image_data = self.client.download_image(page.image)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(image_data)
                logger.debug(f"Downloaded: {output_path.name}")
                return True, ""
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Image download failed (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    error_msg = f"Failed to download {page.image}: {e}"
                    logger.error(error_msg)
                    return False, error_msg

        return False, "Max retries exceeded"

    def download_chapter_images(
        self,
        pages: List[Page],
        output_dir: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> ImageDownloadSummary:
        """Download all chapter images concurrently.

        Args:
            pages: List of Page objects to download
            output_dir: Directory to save images
            progress_callback: Optional callback(completed, total) for progress updates

        Returns:
            ImageDownloadSummary with successful count and failed page details.
            For backward compatibility the summary can also be unpacked as
            ``(successful, errors)``.
        """
        config = get_config()
        concurrent = config.concurrent_images

        output_dir.mkdir(parents=True, exist_ok=True)

        summary = ImageDownloadSummary(total=len(pages))

        def download_task(page: Page) -> Tuple[Page, str, bool, str]:
            # Determine extension from image path
            ext = Path(page.image).suffix or ".webp"
            # Use zero-padded numbering for proper sorting
            filename = f"{page.number:04d}{ext}"
            output_path = output_dir / filename
            success, error = self.download_image_with_retry(page, output_path)
            return page, filename, success, error

        with ThreadPoolExecutor(max_workers=concurrent) as executor:
            futures = {executor.submit(download_task, page): page for page in pages}

            for future in as_completed(futures):
                page, filename, success, error = future.result()
                if success:
                    summary.successful += 1
                else:
                    summary.failed_pages.append(
                        FailedPage(page.number, filename, error)
                    )
                    summary.errors.append(error)

                if progress_callback:
                    progress_callback(summary.successful + summary.failed, summary.total)

        logger.info(
            f"Downloaded {summary.successful}/{summary.total} images "
            f"({summary.failed} failed)"
        )
        return summary

    def close(self):
        """Close resources."""
        if self._owns_client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
