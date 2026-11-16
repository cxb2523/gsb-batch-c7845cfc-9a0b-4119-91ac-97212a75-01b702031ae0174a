import datetime as dt
import enum
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests

COTAHIST_BASE_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist"
DEFAULT_COTAHIST_PATH = "data/landing/b3/cotahist"
COTAHIST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/zip,application/octet-stream,*/*",
}


class CotahistError(Exception):
    """Base class for COTAHIST download errors."""


class CotahistMissingError(CotahistError):
    """Raised when the remote archive does not exist.

    Covers the cases where ``download_file`` used to return ``None`` because
    the slot is simply not published on the B3 site: HTTP 404, an HTML page
    returned instead of the ZIP, or an empty response body.
    """


class CotahistDownloadError(CotahistError):
    """Raised when the download itself fails.

    Covers request exceptions (network outages, timeouts, server errors),
    interrupted writes and invalid ZIP archives. Callers can retry these,
    unlike :class:`CotahistMissingError` slots.
    """


class DownloadStatus(str, enum.Enum):
    """Outcome of a single slot in a batch download."""

    SUCCESS = "success"
    MISSING = "missing"
    FAILED = "failed"


@dataclass(frozen=True)
class CotahistFile:
    series: str
    ref: str
    filename: str

    @property
    def url(self) -> str:
        return f"{COTAHIST_BASE_URL}/{self.filename}"

    @property
    def txt_filename(self) -> str:
        return self.filename.removesuffix(".ZIP") + ".TXT"


@dataclass(frozen=True)
class DownloadResult:
    """Per-slot settlement record returned by the batch functions.

    Attributes:
        ref: Slot identifier (year, ``YYYY-MM`` or ISO date).
        status: Outcome of the download attempt.
        filename: Expected archive filename for the slot.
        path: Destination path when ``status`` is ``SUCCESS``, else ``None``.
        error: Original error message when the slot did not succeed.
    """

    ref: str
    status: DownloadStatus
    filename: str
    path: Path | None = None
    error: str | None = None


def annual_file(year: int) -> CotahistFile:
    """Returns metadata for a yearly COTAHIST file."""
    return CotahistFile(
        series="annual",
        ref=str(year),
        filename=f"COTAHIST_A{year}.ZIP",
    )


def monthly_file(year: int, month: int) -> CotahistFile:
    """Returns metadata for a monthly COTAHIST file."""
    _validate_month(month)
    return CotahistFile(
        series="monthly",
        ref=f"{year}-{month:02d}",
        filename=f"COTAHIST_M{month:02d}{year}.ZIP",
    )


def daily_file(date: dt.date | str) -> CotahistFile:
    """Returns metadata for a daily COTAHIST file."""
    date = _parse_date(date)
    return CotahistFile(
        series="daily",
        ref=date.isoformat(),
        filename=f"COTAHIST_D{date:%d%m%Y}.ZIP",
    )


def is_zip_file(path: str | Path) -> bool:
    """Checks whether a path points to a valid ZIP file."""
    path = Path(path)
    if not path.exists() or path.stat().st_size < 4:
        return False

    with path.open("rb") as file:
        magic = file.read(4)

    return magic == b"PK\x03\x04" and zipfile.is_zipfile(path)


def download_file(
    item: CotahistFile,
    output_dir: str | Path = DEFAULT_COTAHIST_PATH,
    overwrite: bool = False,
    validate_zip: bool = True,
    timeout: int = 60,
) -> Path:
    """Downloads a COTAHIST file.

    The successful paths are unchanged: a previously downloaded valid archive
    is returned directly (without any network request) when ``overwrite`` is
    ``False``, and ZIP validation is skipped when ``validate_zip`` is
    ``False``.

    Raises:
        CotahistMissingError: The slot is not published on the B3 site
            (HTTP 404, HTML response or empty body). Retrying is pointless.
        CotahistDownloadError: The download failed because of a request
            exception, a write interruption or an invalid ZIP archive.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / item.filename

    if output_path.exists() and not overwrite:
        if not validate_zip or is_zip_file(output_path):
            return output_path

    tmp_path = output_path.with_suffix(output_path.suffix + ".part")

    try:
        with requests.get(
            item.url,
            headers=COTAHIST_HEADERS,
            stream=True,
            timeout=timeout,
        ) as response:
            if response.status_code == 404:
                raise CotahistMissingError(
                    f"Archive not found (HTTP 404) for {item.url}"
                )

            response.raise_for_status()

            if _is_html_response(response):
                raise CotahistMissingError(
                    f"Remote returned an HTML page instead of a ZIP archive "
                    f"for {item.url}"
                )

            total_bytes = _write_response_content(response, tmp_path)
            if total_bytes == 0:
                raise CotahistMissingError(
                    f"Remote returned an empty response body for {item.url}"
                )

        tmp_path.replace(output_path)

        if validate_zip and not is_zip_file(output_path):
            _safe_unlink(output_path)
            raise CotahistDownloadError(
                f"Downloaded file is not a valid ZIP archive: {output_path}"
            )

        return output_path

    except CotahistMissingError:
        _safe_unlink(tmp_path)
        raise
    except requests.RequestException as exc:
        _safe_unlink(tmp_path)
        raise CotahistDownloadError(
            f"Failed to download {item.url}: {exc}"
        ) from exc
    except OSError as exc:
        _safe_unlink(tmp_path)
        raise CotahistDownloadError(
            f"Failed to write archive to {output_path}: {exc}"
        ) from exc


def download_annual_series(
    year: int,
    output_dir: str | Path = f"{DEFAULT_COTAHIST_PATH}/annual",
    overwrite: bool = False,
    validate_zip: bool = True,
    timeout: int = 60,
) -> Path:
    """Downloads the yearly COTAHIST series for a year.

    Raises :class:`CotahistMissingError` or :class:`CotahistDownloadError`
    when the archive cannot be obtained; see :func:`download_file`.
    """
    return download_file(
        item=annual_file(year),
        output_dir=output_dir,
        overwrite=overwrite,
        validate_zip=validate_zip,
        timeout=timeout,
    )


def download_monthly_series(
    year: int,
    month: int,
    output_dir: str | Path = f"{DEFAULT_COTAHIST_PATH}/monthly",
    overwrite: bool = False,
    validate_zip: bool = True,
    timeout: int = 60,
) -> Path:
    """Downloads the monthly COTAHIST series for a month.

    Raises :class:`CotahistMissingError` or :class:`CotahistDownloadError`
    when the archive cannot be obtained; see :func:`download_file`.
    """
    return download_file(
        item=monthly_file(year, month),
        output_dir=output_dir,
        overwrite=overwrite,
        validate_zip=validate_zip,
        timeout=timeout,
    )


def download_daily_series(
    date: dt.date | str,
    output_dir: str | Path = f"{DEFAULT_COTAHIST_PATH}/daily",
    overwrite: bool = False,
    validate_zip: bool = True,
    timeout: int = 60,
) -> Path:
    """Downloads the daily COTAHIST series for a date.

    Raises :class:`CotahistMissingError` or :class:`CotahistDownloadError`
    when the archive cannot be obtained; see :func:`download_file`.
    """
    return download_file(
        item=daily_file(date),
        output_dir=output_dir,
        overwrite=overwrite,
        validate_zip=validate_zip,
        timeout=timeout,
    )


def iter_months(
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> Iterable[tuple[int, int]]:
    """Iterates over months in a closed interval."""
    _validate_month(start_month)
    _validate_month(end_month)
    current = dt.date(start_year, start_month, 1)
    end = dt.date(end_year, end_month, 1)

    if current > end:
        raise ValueError("Start month cannot be greater than end month")

    while current <= end:
        yield current.year, current.month

        if current.month == 12:
            current = dt.date(current.year + 1, 1, 1)
        else:
            current = dt.date(current.year, current.month + 1, 1)


def iter_dates(
    start_date: dt.date | str,
    end_date: dt.date | str,
    weekdays_only: bool = False,
) -> Iterable[dt.date]:
    """Iterates over dates in a closed interval."""
    start_date = _parse_date(start_date)
    end_date = _parse_date(end_date)

    if start_date > end_date:
        raise ValueError("Start date cannot be greater than end date")

    current = start_date

    while current <= end_date:
        if not weekdays_only or current.weekday() < 5:
            yield current

        current += dt.timedelta(days=1)


def download_annual_series_range(
    start_year: int,
    end_year: int,
    output_dir: str | Path = f"{DEFAULT_COTAHIST_PATH}/annual",
    overwrite: bool = False,
    validate_zip: bool = True,
    timeout: int = 60,
    sleep: float = 1.0,
    report: bool = False,
) -> list[Path] | list[DownloadResult]:
    """Downloads yearly COTAHIST series within a year range.

    By default returns the list of successfully downloaded paths, exactly as
    before. With ``report=True`` returns a :class:`DownloadResult` per slot
    (including missing and failed ones) instead.
    """
    if start_year > end_year:
        raise ValueError("Start year cannot be greater than end year")

    items = (annual_file(year) for year in range(start_year, end_year + 1))

    return _download_range(
        items=items,
        output_dir=output_dir,
        overwrite=overwrite,
        validate_zip=validate_zip,
        timeout=timeout,
        sleep=sleep,
        report=report,
    )


def download_monthly_series_range(
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
    output_dir: str | Path = f"{DEFAULT_COTAHIST_PATH}/monthly",
    overwrite: bool = False,
    validate_zip: bool = True,
    timeout: int = 60,
    sleep: float = 1.0,
    report: bool = False,
) -> list[Path] | list[DownloadResult]:
    """Downloads monthly COTAHIST series within a month range.

    By default returns the list of successfully downloaded paths, exactly as
    before. With ``report=True`` returns a :class:`DownloadResult` per slot
    (including missing and failed ones) instead.
    """
    items = (
        monthly_file(year, month)
        for year, month in iter_months(
            start_year, start_month, end_year, end_month
        )
    )

    return _download_range(
        items=items,
        output_dir=output_dir,
        overwrite=overwrite,
        validate_zip=validate_zip,
        timeout=timeout,
        sleep=sleep,
        report=report,
    )


def download_daily_series_range(
    start_date: dt.date | str,
    end_date: dt.date | str,
    output_dir: str | Path = f"{DEFAULT_COTAHIST_PATH}/daily",
    weekdays_only: bool = True,
    overwrite: bool = False,
    validate_zip: bool = True,
    timeout: int = 60,
    sleep: float = 1.0,
    report: bool = False,
) -> list[Path] | list[DownloadResult]:
    """Downloads daily COTAHIST series within a date range.

    By default returns the list of successfully downloaded paths, exactly as
    before. With ``report=True`` returns a :class:`DownloadResult` per slot
    (including missing and failed ones) instead.
    """
    items = (
        daily_file(date)
        for date in iter_dates(start_date, end_date, weekdays_only=weekdays_only)
    )

    return _download_range(
        items=items,
        output_dir=output_dir,
        overwrite=overwrite,
        validate_zip=validate_zip,
        timeout=timeout,
        sleep=sleep,
        report=report,
    )


def _download_range(
    items: Iterable[CotahistFile],
    output_dir: str | Path,
    overwrite: bool,
    validate_zip: bool,
    timeout: int,
    sleep: float,
    report: bool,
) -> list[Path] | list[DownloadResult]:
    downloaded: list[Path] = []
    results: list[DownloadResult] = []

    for item in items:
        try:
            path = download_file(
                item=item,
                output_dir=output_dir,
                overwrite=overwrite,
                validate_zip=validate_zip,
                timeout=timeout,
            )
        except CotahistMissingError as exc:
            results.append(
                DownloadResult(
                    ref=item.ref,
                    status=DownloadStatus.MISSING,
                    filename=item.filename,
                    error=str(exc),
                )
            )
        except CotahistDownloadError as exc:
            results.append(
                DownloadResult(
                    ref=item.ref,
                    status=DownloadStatus.FAILED,
                    filename=item.filename,
                    error=str(exc),
                )
            )
        else:
            downloaded.append(path)
            results.append(
                DownloadResult(
                    ref=item.ref,
                    status=DownloadStatus.SUCCESS,
                    filename=item.filename,
                    path=path,
                )
            )

        _sleep(sleep)

    return results if report else downloaded


def _parse_date(date: dt.date | str) -> dt.date:
    if isinstance(date, str):
        return dt.date.fromisoformat(date)
    return date


def _validate_month(month: int) -> None:
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")


def _is_html_response(response: requests.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    return "html" in content_type


def _write_response_content(response: requests.Response, output_path: Path) -> int:
    total_bytes = 0

    with output_path.open("wb") as file:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                file.write(chunk)
                total_bytes += len(chunk)

    return total_bytes


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


__all__ = [
    "CotahistFile",
    "CotahistError",
    "CotahistMissingError",
    "CotahistDownloadError",
    "DownloadStatus",
    "DownloadResult",
    "annual_file",
    "monthly_file",
    "daily_file",
    "is_zip_file",
    "download_file",
    "download_annual_series",
    "download_monthly_series",
    "download_daily_series",
    "iter_months",
    "iter_dates",
    "download_annual_series_range",
    "download_monthly_series_range",
    "download_daily_series_range",
]
