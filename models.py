"""Data models for the Atsu Downloader."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class DownloadStatus(str, Enum):
    """Outcome of a single chapter download.

    ``SUCCESS`` means every image was downloaded and converted.
    ``PARTIAL`` means the chapter exists but at least one image failed, so the
    saved chapter is incomplete.
    ``FAILED`` means the chapter had pages but no image could be downloaded.
    ``NO_PAGES`` means the API returned no pages at all (nothing to download),
    which is intentionally distinct from ``FAILED``.
    """

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    NO_PAGES = "no_pages"


class FailedPage(int):
    """A page number that failed to download, with optional file details.

    Subclasses ``int`` so it can be used directly as a page number (and
    compared with plain integers) while still carrying the generated filename
    and the underlying error for display and manifest output.
    """

    def __new__(
        cls,
        page_number: int,
        filename: str = "",
        error: str = "",
    ) -> "FailedPage":
        obj = super().__new__(cls, int(page_number))
        obj.page_number = int(page_number)
        obj.filename = filename
        obj.error = error
        return obj

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"FailedPage(page_number={self.page_number}, "
            f"filename={self.filename!r})"
        )


@dataclass
class DownloadSummary:
    """Aggregated chapter outcome counts for a batch download."""

    total: int = 0
    successful: int = 0
    partial: int = 0
    failed: int = 0

    @property
    def not_fully_successful(self) -> int:
        """Chapters that need attention (partial or completely failed)."""
        return self.partial + self.failed


def summarize_results(results: List["DownloadResult"]) -> DownloadSummary:
    """Summarize a list of chapter results into success/partial/failed counts."""
    summary = DownloadSummary(total=len(results))
    for result in results:
        if result.status == DownloadStatus.PARTIAL.value:
            summary.partial += 1
        elif result.status == DownloadStatus.SUCCESS.value:
            summary.successful += 1
        else:
            summary.failed += 1
    return summary


@dataclass
class Page:
    """Represents a single page/image in a chapter."""
    id: str
    image: str
    number: int
    width: int = 0
    height: int = 0
    aspect_ratio: float = 0.0

    @property
    def full_url(self) -> str:
        """Get the full image URL."""
        return f"https://atsu.moe{self.image}"



@dataclass
class Chapter:
    """Represents a manga chapter."""
    id: str
    title: str
    number: float
    index: int
    page_count: int
    scanlation_manga_id: str = ""
    pages: List[Page] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Chapter":
        """Create a Chapter from API response dict."""
        number = data.get("number")
        if number is None:
            number = 0.0
            
        return cls(
            id=data["id"],
            title=data["title"],
            number=float(number),
            index=data.get("index", 0),
            page_count=data.get("pageCount", 0),
            scanlation_manga_id=data.get("scanlationMangaId") or data.get("scanId") or ""
        )


@dataclass
class Scanlator:
    """Represents a scanlation group."""
    id: str
    name: str

    @classmethod
    def from_dict(cls, data: dict) -> "Scanlator":
        """Create a Scanlator from API response dict."""
        return cls(
            id=data["id"],
            name=data["name"]
        )


@dataclass
class MangaInfo:
    """Represents manga information and chapters."""
    id: str
    title: str
    manga_type: str
    synopsis: str = ""
    cover_url: str = ""
    genres: List[str] = field(default_factory=list)
    authors: List[str] = field(default_factory=list)
    status: str = "Unknown"
    scanlators: List[Scanlator] = field(default_factory=list)
    chapters: List[Chapter] = field(default_factory=list)


    @classmethod
    def from_dict(cls, data: dict) -> "MangaInfo":
        """Create MangaInfo from API response dict."""
        # Handle cases where data is nested under 'mangaPage'
        manga = data.get("mangaPage") or data
        
        chapters: List[Chapter] = []
        seen_chapter_ids = set()
        seen_chapter_keys = set()

        def normalize_number(value: float) -> str:
            if value == int(value):
                return str(int(value))
            return f"{value:.6f}".rstrip("0").rstrip(".")

        # Process chapters if present
        for ch in manga.get("chapters", []):
            chapter_id = ch.get("id")
            if not chapter_id or chapter_id in seen_chapter_ids:
                continue

            chapter = Chapter.from_dict(ch)
            chapter_key = (
                normalize_number(chapter.number),
                chapter.title.strip().lower(),
            )

            if chapter_key in seen_chapter_keys:
                continue

            seen_chapter_ids.add(chapter_id)
            seen_chapter_keys.add(chapter_key)
            chapters.append(chapter)

        # Parse cover URL if possible
        poster = manga.get("poster") or manga.get("image")
        cover_url = ""
        if isinstance(poster, dict):
            poster = poster.get("image")
        if isinstance(poster, str):
            poster = poster.lstrip("/")
            if poster.startswith("static/"):
                poster = poster[len("static/") :]
            cover_url = f"https://atsu.moe/static/{poster}"

        return cls(
            id=manga["id"],
            title=manga.get("title") or manga.get("englishTitle") or "Unknown",
            manga_type=manga.get("type", "Unknown"),
            synopsis=manga.get("synopsis", ""),
            cover_url=cover_url,
            genres=[g.get("name") for g in manga.get("genres") or [] if g.get("name")] or \
                   [t.get("name") for t in manga.get("tags") or [] if t.get("name")] or [],
            authors=[a.get("name") for a in manga.get("authors") or [] if a.get("name")] or [],
            scanlators=[Scanlator.from_dict(s) for s in manga.get("scanlators") or []],
            status=manga.get("status", "Unknown"),
            chapters=chapters
        )



@dataclass
class DownloadResult:
    """Result of a download operation.

    ``success`` is only ``True`` when the chapter is fully complete. A chapter
    with some failed images is reported with ``status == PARTIAL`` and
    ``success == False``; ``failed_pages`` additionally identifies which page
    numbers (and generated filenames) are missing so the chapter can be
    redownloaded.
    """

    success: bool
    chapter: Chapter
    output_path: str
    error: Optional[str] = None
    images_downloaded: int = 0
    images_failed: int = 0
    failed_pages: List[FailedPage] = field(default_factory=list)
    total_images: int = 0
    status: Optional[str] = None

    def __post_init__(self) -> None:
        normalized: List[FailedPage] = []
        for page in self.failed_pages:
            if isinstance(page, FailedPage):
                normalized.append(page)
            else:
                normalized.append(FailedPage(int(page)))
        self.failed_pages = normalized
        if self.images_failed == 0 and normalized:
            self.images_failed = len(normalized)
        if self.status is None:
            self.status = (
                DownloadStatus.SUCCESS.value
                if self.success
                else DownloadStatus.FAILED.value
            )

    @property
    def is_partial(self) -> bool:
        """True when the chapter was saved but some images are missing."""
        return self.status == DownloadStatus.PARTIAL.value

    @property
    def is_complete(self) -> bool:
        """True only when the chapter downloaded in full."""
        return self.status == DownloadStatus.SUCCESS.value

    @property
    def failed_page_numbers(self) -> List[int]:
        """Failed page numbers as plain integers, sorted ascending."""
        return sorted(int(p) for p in self.failed_pages)

    def describe_status(self) -> str:
        """Human-readable one-line status for logs and summaries."""
        chapter_label = f"Chapter {format_chapter_number(self.chapter.number)}"
        if self.status == DownloadStatus.PARTIAL.value:
            pages = ", ".join(str(n) for n in self.failed_page_numbers)
            return (
                f"⚠ {chapter_label} partially downloaded: "
                f"{self.images_downloaded}/{self.total_images} images, "
                f"missing page(s) {pages}"
            )
        if self.status == DownloadStatus.NO_PAGES.value:
            return f"✗ {chapter_label} has no pages to download"
        if self.status == DownloadStatus.SUCCESS.value:
            return (
                f"✓ {chapter_label} downloaded "
                f"({self.images_downloaded} images)"
            )
        detail = f": {self.error}" if self.error else ""
        return f"✗ {chapter_label} failed{detail}"


def format_chapter_number(number: float) -> str:
    """Format a chapter number without a trailing ``.0``."""
    try:
        if number == int(number):
            return str(int(number))
    except (TypeError, ValueError):
        pass
    return str(number)
