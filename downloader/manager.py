"""Download manager for orchestrating chapter downloads."""

import json
import logging
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from api.client import AtsuClient
from config import get_config
from converters import convert_to_pdf, convert_to_cbz
from models import (
    Chapter,
    DownloadResult,
    DownloadStatus,
    MangaInfo,
    format_chapter_number,
    summarize_results,
)

from .images import ImageDownloader

logger = logging.getLogger(__name__)

#: Default filename for the retry manifest written into the download directory.
RETRY_MANIFEST_FILENAME = "retry_manifest.json"


def build_retry_manifest(
    manga: MangaInfo,
    results: List[DownloadResult]
) -> dict:
    """Build the retry manifest payload for a batch of chapter results.

    Only chapters with at least one failed image are included. Each entry
    identifies the chapter (number, title, id), the generated output path and
    the page numbers/filenames that are missing, so the chapter can be
    downloaded again.
    """
    retry_chapters = []
    for result in results:
        if result.images_failed <= 0:
            continue

        ordered_failures = sorted(
            result.failed_pages, key=lambda page: int(page)
        )
        retry_chapters.append({
            "chapter_id": result.chapter.id,
            "chapter_number": format_chapter_number(result.chapter.number),
            "chapter_title": result.chapter.title,
            "status": result.status,
            "output_path": result.output_path,
            "images_downloaded": result.images_downloaded,
            "images_failed": result.images_failed,
            "total_images": result.total_images,
            "failed_pages": [int(page) for page in ordered_failures],
            "failed_files": [
                page.filename for page in ordered_failures if page.filename
            ],
        })

    # Stable ordering makes the manifest easy to diff and re-run.
    retry_chapters.sort(
        key=lambda entry: (float(entry["chapter_number"]), entry["chapter_title"])
    )

    return {
        "manga_id": manga.id,
        "manga_title": manga.title,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_chapters": len(results),
        "retry_chapters": retry_chapters,
        "retry_chapter_numbers": [
            entry["chapter_number"] for entry in retry_chapters
        ],
    }


def _load_existing_entries(
    manga: MangaInfo,
    base_dir: Optional[Path]
) -> List[dict]:
    """Load retry entries from an existing manifest for the same manga."""
    try:
        if base_dir is None:
            base_dir = Path(get_config().download_path)
        manifest_path = Path(base_dir) / RETRY_MANIFEST_FILENAME
        if not manifest_path.exists():
            return []
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("manga_id") != manga.id:
            return []
        return list(existing.get("retry_chapters", []))
    except Exception as e:  # Reporting failures must never raise
        logger.error(f"Failed to read existing retry manifest: {e}")
        return []


def _merge_retry_entries(
    previous: List[dict],
    current: List[dict],
    attempted_ids: set
) -> List[dict]:
    """Merge new retry entries into a previous manifest.

    Chapters attempted in this run are fully replaced by their new outcome
    (so a now-complete chapter disappears). Chapters that were not part of
    this run are carried over, so retrying a subset never drops other pending
    chapters.
    """
    carried = [
        entry for entry in previous
        if entry.get("chapter_id") not in attempted_ids
    ]
    merged = carried + current
    merged.sort(
        key=lambda entry: (
            float(entry.get("chapter_number", 0)),
            entry.get("chapter_title", ""),
        )
    )
    return merged


def write_retry_manifest(
    manga: MangaInfo,
    results: List[DownloadResult],
    base_dir: Optional[Path] = None
) -> Optional[Path]:
    """Write a retry manifest for chapters with failed images.

    The manifest is written to ``base_dir / retry_manifest.json`` (the download
    directory by default). Entries from a previous manifest for the same manga
    are merged in so retrying a subset does not lose other pending chapters.
    When there is nothing left to retry, no manifest is written (any stale one
    for this manga is removed).

    Returns:
        Path to the written manifest, or ``None`` when nothing was written.
        Write errors are logged and swallowed so they never discard or mask
        the download results that were already produced.
    """
    try:
        manifest = build_retry_manifest(manga, results)
    except Exception as e:  # Defensive: never let reporting break downloads
        logger.exception(f"Failed to build retry manifest: {e}")
        return None

    if base_dir is None:
        base_dir = Path(get_config().download_path)
    manifest_path = Path(base_dir) / RETRY_MANIFEST_FILENAME

    attempted_ids = {result.chapter.id for result in results}
    retry_chapters = _merge_retry_entries(
        _load_existing_entries(manga, base_dir),
        manifest["retry_chapters"],
        attempted_ids,
    )

    if not retry_chapters:
        # Nothing left to retry for this manga; drop any stale manifest.
        try:
            if manifest_path.exists():
                manifest_path.unlink()
                logger.info(f"Removed stale retry manifest: {manifest_path}")
        except Exception as e:  # Reporting failures must never raise
            logger.error(f"Failed to remove stale retry manifest: {e}")
        logger.info("All chapters complete; no retry manifest needed")
        return None

    manifest["retry_chapters"] = retry_chapters
    manifest["retry_chapter_numbers"] = [
        entry["chapter_number"] for entry in retry_chapters
    ]

    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception as e:
        logger.error(f"Failed to write retry manifest {manifest_path}: {e}")
        return None

    logger.warning(
        f"{len(retry_chapters)} chapter(s) incomplete; "
        f"retry manifest written to {manifest_path}"
    )
    return manifest_path


def parse_chapter_selection(selection: str, total_chapters: int) -> List[int]:
    """Parse chapter selection string into list of indices.
    
    Supports:
    - "all" or "a": All chapters
    - "5": Single chapter (by number)
    - "1-10": Range of chapters
    - "1,3,5-7": Mixed selection
    
    Returns:
        List of 0-based chapter indices
    """
    selection = selection.strip().lower()
    
    if selection in ("all", "a"):
        return list(range(total_chapters))
    
    indices = set()
    parts = selection.replace(" ", "").split(",")
    
    for part in parts:
        if "-" in part:
            # Range: "1-10"
            match = re.match(r"(\d+)-(\d+)", part)
            if match:
                start, end = int(match.group(1)), int(match.group(2))
                # Convert to 0-based indices
                for i in range(start - 1, min(end, total_chapters)):
                    if 0 <= i < total_chapters:
                        indices.add(i)
        else:
            # Single: "5"
            try:
                num = int(part)
                idx = num - 1  # Convert to 0-based
                if 0 <= idx < total_chapters:
                    indices.add(idx)
            except ValueError:
                continue
    
    return sorted(indices)


def sanitize_filename(name: str) -> str:
    """Sanitize string for use as filename."""
    # Remove invalid characters
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Replace multiple spaces with single space
    name = re.sub(r'\s+', ' ', name)
    return name.strip()[:200]  # Limit length


class DownloadManager:
    """Orchestrates downloading of manga chapters."""

    def __init__(self, client: Optional[AtsuClient] = None):
        self.client = client or AtsuClient()
        self._owns_client = client is None
        self.image_downloader = ImageDownloader(self.client)

    def download_chapter(
        self,
        manga: MangaInfo,
        chapter: Chapter,
        base_dir: Path,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> DownloadResult:
        """Download a single chapter.
        
        Args:
            manga: MangaInfo object
            chapter: Chapter to download
            base_dir: Base download directory
            progress_callback: Optional callback(status, completed, total)
            
        Returns:
            DownloadResult with success status and output path
        """
        config = get_config()
        
        # Create chapter directory
        manga_dir = base_dir / sanitize_filename(manga.title)
        
        # Format chapter number properly (handle decimals)
        if chapter.number == int(chapter.number):
            chapter_num = str(int(chapter.number))
        else:
            chapter_num = str(chapter.number)
        
        chapter_name = f"chapter {chapter_num}"
        chapter_dir = manga_dir / sanitize_filename(chapter_name)
        
        try:
            # Fetch chapter pages
            logger.info(f"Fetching pages for {chapter.title}")
            pages = self.client.get_chapter_pages(manga.id, chapter.id)

            if not pages:
                return DownloadResult(
                    success=False,
                    chapter=chapter,
                    output_path=str(chapter_dir),
                    error="No pages found in chapter",
                    status=DownloadStatus.NO_PAGES.value
                )

            # Download images
            def image_progress(completed: int, total: int):
                if progress_callback:
                    progress_callback(f"Downloading {chapter.title}", completed, total)

            image_summary = self.image_downloader.download_chapter_images(
                pages, chapter_dir, image_progress
            )
            successful = image_summary.successful

            if successful == 0:
                return DownloadResult(
                    success=False,
                    chapter=chapter,
                    output_path=str(chapter_dir),
                    error=f"Failed to download any images: {image_summary.errors}",
                    images_downloaded=0,
                    images_failed=image_summary.failed,
                    failed_pages=image_summary.failed_pages,
                    total_images=image_summary.total,
                    status=DownloadStatus.FAILED.value
                )

            # Convert format if needed
            output_path = str(chapter_dir)

            if config.download_format == "pdf":
                pdf_path = chapter_dir.with_suffix(".pdf")
                convert_to_pdf(chapter_dir, pdf_path)
                output_path = str(pdf_path)

                if not config.keep_images:
                    shutil.rmtree(chapter_dir)

            elif config.download_format == "cbz":
                cbz_path = chapter_dir.with_suffix(".cbz")
                convert_to_cbz(chapter_dir, cbz_path, manga, chapter)
                output_path = str(cbz_path)

                if not config.keep_images:
                    shutil.rmtree(chapter_dir)

            # A chapter with any failed image is only partially successful: the
            # saved file is missing pages, so it must not be reported as a
            # complete success.
            if image_summary.failed > 0:
                return DownloadResult(
                    success=False,
                    chapter=chapter,
                    output_path=output_path,
                    error=(
                        f"Incomplete: {image_summary.failed}/{image_summary.total} "
                        f"images failed"
                    ),
                    images_downloaded=successful,
                    images_failed=image_summary.failed,
                    failed_pages=image_summary.failed_pages,
                    total_images=image_summary.total,
                    status=DownloadStatus.PARTIAL.value
                )

            return DownloadResult(
                success=True,
                chapter=chapter,
                output_path=output_path,
                images_downloaded=successful,
                images_failed=0,
                total_images=image_summary.total,
                status=DownloadStatus.SUCCESS.value
            )

        except Exception as e:
            logger.exception(f"Failed to download chapter: {e}")
            return DownloadResult(
                success=False,
                chapter=chapter,
                output_path=str(chapter_dir),
                error=str(e),
                status=DownloadStatus.FAILED.value
            )

    def download_chapters(
        self,
        manga: MangaInfo,
        chapter_indices: List[int],
        progress_callback: Optional[Callable[[str, int, int, int, int], None]] = None
    ) -> List[DownloadResult]:
        """Download multiple chapters concurrently.
        
        Args:
            manga: MangaInfo object
            chapter_indices: 0-based indices of chapters to download
            progress_callback: Optional callback(chapter_title, chapter_num, total_chapters, images_done, total_images)
            
        Returns:
            List of DownloadResult objects
        """
        config = get_config()
        base_dir = Path(config.download_path)
        concurrent_chapters = config.concurrent_chapters
        
        chapters = [manga.chapters[i] for i in chapter_indices if i < len(manga.chapters)]
        total_chapters = len(chapters)
        
        results: List[DownloadResult] = []
        completed_chapters = 0
        
        def download_task(chapter: Chapter) -> DownloadResult:
            nonlocal completed_chapters
            
            def chapter_progress(status: str, images_done: int, total_images: int):
                if progress_callback:
                    progress_callback(
                        status,
                        completed_chapters + 1,
                        total_chapters,
                        images_done,
                        total_images
                    )
            
            result = self.download_chapter(manga, chapter, base_dir, chapter_progress)
            completed_chapters += 1
            return result
        
        with ThreadPoolExecutor(max_workers=concurrent_chapters) as executor:
            futures = {executor.submit(download_task, ch): ch for ch in chapters}

            for future in as_completed(futures):
                results.append(future.result())

        # Report partial failures and leave a retry list for the next run.
        summary = summarize_results(results)
        if summary.not_fully_successful:
            logger.warning(
                f"Batch finished with {summary.partial} partially downloaded "
                f"and {summary.failed} failed chapter(s)"
            )
        write_retry_manifest(manga, results, base_dir)

        return results

    def close(self):
        """Close resources."""
        self.image_downloader.close()
        if self._owns_client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
