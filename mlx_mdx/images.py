"""Image downloading and validation utilities.

Each candidate image is processed in isolation: a timeout, a non-image
response, an oversized payload or a disk error is recorded as a terminal
outcome for that candidate and never aborts the remaining images or the
article Markdown.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from filetype import guess

from .models import (
    ImageAsset,
    ImageCandidate,
    ImageDownloadResult,
    ImageOutcome,
    ImageOutcomeStatus,
)
from .utils import slugify

logger = logging.getLogger("mlx_mdx")

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MIN_IMAGE_BYTES = 512
ALLOWED_IMAGE_TYPES = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff"}
DEFAULT_REQUEST_TIMEOUT = 15
_DOWNLOAD_CHUNK_BYTES = 64 * 1024

# Content types that only describe an opaque byte stream; a real image
# signature is still required, but the header itself is not informative.
_OPAQUE_CONTENT_TYPES = {
    "application/octet-stream",
    "binary/octet-stream",
    "application/binary",
}


def detect_image_format(data: bytes) -> Optional[str]:
    """Detect image type using filetype; returns lowercase extension."""
    kind = guess(data)
    if kind and kind.mime.startswith("image/"):
        ext = kind.extension.lower()
        if ext == "jpeg":
            return "jpg"
        return ext
    return None


def infer_image_extension(content_type: Optional[str], data: bytes) -> Optional[str]:
    """Guess an image file extension from HTTP metadata or file signature."""
    detected = detect_image_format(data)
    if detected:
        return detected
    if not content_type:
        return None
    parts = content_type.split(";")[0].split("/")
    if len(parts) == 2 and parts[0] == "image":
        ext = parts[1].strip().lower()
        if ext == "jpeg":
            ext = "jpg"
        return ext
    return None


def _normalise_content_type(content_type: Optional[str]) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def _format_request_error(exc: requests.RequestException) -> str:
    if isinstance(exc, requests.exceptions.Timeout):
        return f"request timed out ({exc.__class__.__name__})"
    message = str(exc).strip()
    return f"{exc.__class__.__name__}: {message}" if message else exc.__class__.__name__


def _read_limited_body(
    response: requests.Response,
    max_bytes: int,
) -> Tuple[Optional[bytes], int]:
    """Read at most ``max_bytes`` bytes from a streamed response.

    Returns ``(body, size)`` where ``body`` is ``None`` when the payload
    exceeds ``max_bytes``. At most ``max_bytes + chunk`` bytes are ever
    retained, so a hostile or misconfigured server cannot exhaust memory.
    """
    iter_content = getattr(response, "iter_content", None)
    if iter_content is None:  # pragma: no cover - defensive for test doubles
        body: bytes = response.content
        total = len(body)
        return (None, total) if total > max_bytes else (body, total)

    chunks: List[bytes] = []
    total = 0
    for chunk in iter_content(chunk_size=_DOWNLOAD_CHUNK_BYTES):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            return None, total
        chunks.append(chunk)
    return b"".join(chunks), total


def _close_response(response: requests.Response) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # pragma: no cover - best-effort cleanup
            logger.debug("Failed to close image response", exc_info=True)


def _outcome(
    candidate: ImageCandidate,
    status: ImageOutcomeStatus,
    detail: str,
    relative_path: Optional[str] = None,
) -> ImageOutcome:
    return ImageOutcome(
        original_src=candidate.original_src,
        absolute_url=candidate.absolute_url,
        alt_text=candidate.alt_text,
        status=status,
        detail=detail,
        relative_path=relative_path,
    )


def _download_one(
    session: requests.Session,
    candidate: ImageCandidate,
    image_dir: Path,
    stem: str,
    request_timeout: float,
) -> Tuple[Optional[ImageAsset], ImageOutcome]:
    """Fetch, validate and persist a single image candidate.

    Returns ``(asset, outcome)``. ``asset`` is ``None`` for every non-saved
    outcome and ``outcome.status`` always states why.
    """
    try:
        response = session.get(
            candidate.absolute_url, timeout=request_timeout, stream=True
        )
    except (
        requests.exceptions.MissingSchema,
        requests.exceptions.InvalidSchema,
        requests.exceptions.InvalidURL,
    ) as exc:
        return None, _outcome(
            candidate, ImageOutcomeStatus.SKIPPED_INVALID_URL, _format_request_error(exc)
        )
    except requests.RequestException as exc:
        return None, _outcome(
            candidate,
            ImageOutcomeStatus.SKIPPED_REQUEST_FAILED,
            _format_request_error(exc),
        )
    except Exception as exc:  # noqa: BLE001 - one image must not abort the page
        logger.warning(
            "Unexpected error fetching image %s: %s", candidate.absolute_url, exc
        )
        return None, _outcome(
            candidate,
            ImageOutcomeStatus.SKIPPED_UNEXPECTED_ERROR,
            f"{exc.__class__.__name__}: {exc}",
        )

    try:
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            return None, _outcome(
                candidate,
                ImageOutcomeStatus.SKIPPED_REQUEST_FAILED,
                _format_request_error(exc),
            )

        content_type = _normalise_content_type(response.headers.get("Content-Type", ""))
        declared_length = response.headers.get("Content-Length")
        if declared_length and declared_length.isdigit():
            declared_bytes = int(declared_length)
            if declared_bytes > MAX_IMAGE_BYTES:
                return None, _outcome(
                    candidate,
                    ImageOutcomeStatus.SKIPPED_TOO_LARGE,
                    f"Content-Length {declared_bytes} exceeds limit {MAX_IMAGE_BYTES}",
                )

        data, size = _read_limited_body(response, MAX_IMAGE_BYTES)
        if data is None:
            return None, _outcome(
                candidate,
                ImageOutcomeStatus.SKIPPED_TOO_LARGE,
                f"payload exceeds limit {MAX_IMAGE_BYTES}",
            )
        if size < MIN_IMAGE_BYTES:
            return None, _outcome(
                candidate,
                ImageOutcomeStatus.SKIPPED_TOO_SMALL,
                f"payload size {size} below minimum {MIN_IMAGE_BYTES}",
            )

        extension = detect_image_format(data)
        if extension is None:
            detail = "response body is not a recognised image format"
            if content_type and content_type not in _OPAQUE_CONTENT_TYPES:
                detail += f" (Content-Type={content_type})"
            return None, _outcome(
                candidate, ImageOutcomeStatus.SKIPPED_UNSUPPORTED_TYPE, detail
            )
        if extension.lower() not in ALLOWED_IMAGE_TYPES:
            return None, _outcome(
                candidate,
                ImageOutcomeStatus.SKIPPED_UNSUPPORTED_TYPE,
                f"image type '{extension}' is not allowed",
            )

        filename = f"{stem}.{extension}"
        destination = image_dir / filename
        try:
            destination.write_bytes(data)
        except OSError as exc:
            # Never leave a truncated file behind for a failed write.
            try:
                destination.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - best-effort cleanup
                logger.debug("Failed to remove partial image %s", destination)
            return None, _outcome(
                candidate,
                ImageOutcomeStatus.SKIPPED_WRITE_FAILED,
                f"{exc.__class__.__name__}: {exc}",
            )

        asset = ImageAsset(
            original_src=candidate.original_src,
            absolute_url=candidate.absolute_url,
            alt_text=candidate.alt_text,
            filename=filename,
            relative_path=(Path("images") / filename).as_posix(),
        )
        return asset, _outcome(
            candidate,
            ImageOutcomeStatus.SAVED,
            f"wrote {size} bytes to {asset.relative_path}",
            relative_path=asset.relative_path,
        )
    except Exception as exc:  # noqa: BLE001 - one image must not abort the page
        logger.warning(
            "Unexpected error processing image %s: %s", candidate.absolute_url, exc
        )
        return None, _outcome(
            candidate,
            ImageOutcomeStatus.SKIPPED_UNEXPECTED_ERROR,
            f"{exc.__class__.__name__}: {exc}",
        )
    finally:
        _close_response(response)


def download_images(
    candidates: List[ImageCandidate],
    output_dir: Path,
    *,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> ImageDownloadResult:
    """Download images referenced by the article and persist them locally.

    The returned :class:`ImageDownloadResult` exposes one outcome per input
    candidate (in order, including duplicates) plus the subset of assets that
    were actually written to ``<output_dir>/images``. Candidates sharing an
    absolute URL reuse the first stored file, and every outcome records why a
    non-saved image was skipped.
    """
    result = ImageDownloadResult()
    if not candidates:
        return result

    image_dir = output_dir / "images"
    try:
        image_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        detail = f"{exc.__class__.__name__}: {exc}"
        logger.warning("Failed to create image directory %s: %s", image_dir, exc)
        for candidate in candidates:
            result.outcomes.append(
                _outcome(
                    candidate,
                    ImageOutcomeStatus.SKIPPED_WRITE_FAILED,
                    f"could not create images directory: {detail}",
                )
            )
        return result

    session = requests.Session()
    downloaded: Dict[str, ImageAsset] = {}
    used_filenames: set[str] = set()

    try:
        for index, candidate in enumerate(candidates, start=1):
            if candidate.original_src.startswith(
                "data:"
            ) or candidate.absolute_url.startswith("data:"):
                result.outcomes.append(
                    _outcome(
                        candidate,
                        ImageOutcomeStatus.SKIPPED_DATA_URI,
                        "inline data: URI is not downloaded",
                    )
                )
                continue

            existing = downloaded.get(candidate.absolute_url)
            if existing is not None:
                result.outcomes.append(
                    _outcome(
                        candidate,
                        ImageOutcomeStatus.SAVED,
                        f"reused {existing.relative_path}",
                        relative_path=existing.relative_path,
                    )
                )
                continue

            alt_slug = slugify(candidate.alt_text or "image", fallback="image")
            base_stem = f"image-{index:02d}-{alt_slug}"[:80].strip("-") or f"image-{index:02d}"
            # Guard against a stem collision after truncation or repeated alt
            # text so an already saved file is never overwritten.
            stem = base_stem
            suffix = 1
            while any(name.startswith(f"{stem}.") for name in used_filenames):
                suffix += 1
                stem = f"{base_stem}-{suffix}"

            asset, outcome = _download_one(
                session, candidate, image_dir, stem, request_timeout
            )
            if asset is not None:
                used_filenames.add(asset.filename)
                downloaded[candidate.absolute_url] = asset
                result.assets.append(asset)
            result.outcomes.append(outcome)
    finally:
        session.close()

    saved = len(result.saved_outcomes)
    if saved != len(result.outcomes):
        logger.info(
            "Stored %d/%d image candidates (%d skipped)",
            saved,
            len(result.outcomes),
            len(result.outcomes) - saved,
        )
    return result
