"""Data models used throughout the crawler pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, List, Optional


@dataclass
class PageMetadata:
    """Metadata describing the crawled page."""

    source_url: str
    title: Optional[str]
    description: Optional[str]
    byline: Optional[str]


@dataclass
class ImageCandidate:
    """Raw image reference discovered while parsing article content."""

    original_src: str
    absolute_url: str
    alt_text: str


@dataclass
class ImageAsset:
    """Downloaded and validated image asset stored on disk."""

    original_src: str
    absolute_url: str
    alt_text: str
    filename: str
    relative_path: str


class ImageOutcomeStatus(str, Enum):
    """Terminal state for a single image candidate.

    Every candidate passed to :func:`mlx_mdx.images.download_images` receives
    exactly one of these values, so callers can tell a saved image apart from
    one that was skipped and why.
    """

    SAVED = "saved"
    SKIPPED_DATA_URI = "skipped_data_uri"
    SKIPPED_INVALID_URL = "skipped_invalid_url"
    SKIPPED_REQUEST_FAILED = "skipped_request_failed"
    SKIPPED_TOO_SMALL = "skipped_too_small"
    SKIPPED_TOO_LARGE = "skipped_too_large"
    SKIPPED_UNSUPPORTED_TYPE = "skipped_unsupported_type"
    SKIPPED_WRITE_FAILED = "skipped_write_failed"
    SKIPPED_UNEXPECTED_ERROR = "skipped_unexpected_error"


@dataclass
class ImageOutcome:
    """The outcome of processing one image candidate."""

    original_src: str
    absolute_url: str
    alt_text: str
    status: ImageOutcomeStatus
    detail: str = ""
    relative_path: Optional[str] = None

    @property
    def saved(self) -> bool:
        """Return ``True`` when the candidate is backed by a local file."""
        return self.status is ImageOutcomeStatus.SAVED

    def to_dict(self) -> dict[str, Optional[str]]:
        """Return a JSON-serialisable representation of the outcome."""
        return {
            "original_src": self.original_src,
            "absolute_url": self.absolute_url,
            "alt_text": self.alt_text,
            "status": self.status.value,
            "detail": self.detail,
            "relative_path": self.relative_path,
        }


@dataclass
class ImageDownloadResult:
    """Aggregate result of processing all image candidates for a page.

    ``assets`` contains one entry per locally stored file (duplicates by URL
    share a single asset), while ``outcomes`` contains one entry per candidate
    in input order.
    """

    assets: List[ImageAsset] = field(default_factory=list)
    outcomes: List[ImageOutcome] = field(default_factory=list)

    @property
    def saved_outcomes(self) -> List[ImageOutcome]:
        """Return outcomes for candidates that were stored locally."""
        return [outcome for outcome in self.outcomes if outcome.saved]

    @property
    def skipped_outcomes(self) -> List[ImageOutcome]:
        """Return outcomes for candidates that were not stored locally."""
        return [outcome for outcome in self.outcomes if not outcome.saved]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable summary and per-candidate outcomes."""
        return {
            "totals": {
                "candidates": len(self.outcomes),
                "saved": len(self.saved_outcomes),
                "skipped": len(self.skipped_outcomes),
                "files": len(self.assets),
            },
            "images": [outcome.to_dict() for outcome in self.outcomes],
        }

    # List-like access keeps compatibility with callers that previously
    # received a bare ``List[ImageAsset]`` from ``download_images``.
    def __iter__(self) -> Iterator[ImageAsset]:
        return iter(self.assets)

    def __len__(self) -> int:
        return len(self.assets)

    def __getitem__(self, index):
        return self.assets[index]
