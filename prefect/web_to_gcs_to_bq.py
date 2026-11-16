import io
import zipfile
import datetime as dt
from dataclasses import dataclass, asdict

import pandas as pd
import requests
from prefect import flow, get_run_logger, task
from prefect_gcp import GcpCredentials
from prefect_gcp.cloud_storage import GcsBucket
# from prefect_dbt.cli import DbtCoreOperation, DbtCliProfile


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BUCKET_NAME = "divvy_data_lake_tidal-cipher-381616"
GCS_BUCKET_BLOCK = "divvy-creds-bucket"
GCS_CREDENTIALS_BLOCK = "divvy-creds"
GCS_PREFIX = "divvy-tripdata/"
BQ_DESTINATION_TABLE = "divvy_data_raw.divvy_default_data"
BQ_PROJECT_ID = "tidal-cipher-381616"

# Divvy publishes monthly archives from 2020-04 onwards.
START_MONTH = "202004"
END_MONTH = "202212"

# Status values used in the per-month settlement.
STATUS_SUCCESS = "success"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

HTTP_TIMEOUT_SECONDS = 60
# Retries only for transient network errors (connection reset, DNS hiccup...).
# HTTP error pages and corrupt archives are not retried: they are recorded as
# failed months so the rest of the pipeline can continue.
NETWORK_MAX_RETRIES = 2


class DownloadError(Exception):
    """Raised when a monthly archive cannot be obtained or verified."""


class NoUsableDataError(Exception):
    """Raised when no month produced a usable parquet file for the BQ load."""


@dataclass
class MonthResult:
    """Settlement record for a single month."""

    month: str
    status: str  # one of STATUS_SUCCESS / STATUS_SKIPPED / STATUS_FAILED
    url: str
    gcs_blob: str | None
    reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Pure helpers (easy to unit-test without any Prefect/GCP/network dependency)
# ---------------------------------------------------------------------------

def _parse_month(month: str) -> dt.date:
    if len(month) != 6 or not month.isdigit():
        raise ValueError(f"month must be a YYYYMM string, got {month!r}")
    year, month_no = int(month[:4]), int(month[4:])
    if not 1 <= month_no <= 12:
        raise ValueError(f"month part must be between 01 and 12, got {month!r}")
    return dt.date(year, month_no, 1)


def iter_year_months(start_month: str = START_MONTH,
                     end_month: str = END_MONTH) -> list[str]:
    """Return every YYYYMM between two inclusive bounds, ascending."""
    start = _parse_month(start_month)
    end = _parse_month(end_month)
    if start > end:
        raise ValueError(
            f"start_month {start_month} is later than end_month {end_month}"
        )

    months: list[str] = []
    current = start
    while current <= end:
        months.append(f"{current.year}{current.month:02d}")
        if current.month == 12:
            current = dt.date(current.year + 1, 1, 1)
        else:
            current = dt.date(current.year, current.month + 1, 1)
    return months


def build_url(month: str) -> str:
    _parse_month(month)
    return (
        "https://divvy-tripdata.s3.amazonaws.com/"
        f"{month}-divvy-tripdata.zip"
    )


def gcs_base_path(month: str) -> str:
    """GCS object name without the serialization suffix.

    GcsBucket.upload_from_dataframe(..., serialization_format='parquet_snappy')
    appends '.snappy', so the actual blob is '<base>.parquet.snappy'.
    """
    _parse_month(month)
    return f"{GCS_PREFIX}{month}-divvy-tripdata.parquet"


def gcs_blob_name(month: str) -> str:
    return gcs_base_path(month) + ".snappy"


def is_month_uploaded(month: str, existing_blobs: set[str]) -> bool:
    """True when the month's parquet already exists in GCS.

    The GCS listing holds full object names; prefect_gcp appends '.snappy'
    for parquet_snappy uploads, but tolerate both naming conventions.
    """
    base = gcs_base_path(month)
    return base in existing_blobs or (base + ".snappy") in existing_blobs


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@task(log_prints=True, name="Listing existing GCS blobs")
def list_gcs_blobs(prefix: str = GCS_PREFIX) -> set[str]:
    """List every object name under the given GCS prefix."""
    gcs_block = GcsBucket.load(GCS_BUCKET_BLOCK)
    return {blob.name for blob in gcs_block.list_blobs(prefix)}


@task(log_prints=True, name="Fetch divvy data")
def download_file(url: str,
                  timeout: int = HTTP_TIMEOUT_SECONDS,
                  max_retries: int = NETWORK_MAX_RETRIES) -> io.BytesIO:
    """Download a monthly archive and verify it is really a usable zip.

    Raises DownloadError on non-200 responses, non-zip content (e.g. an S3
    error page returned with 200) or corrupt archives. Only transient network
    errors are retried.
    """
    logger = get_run_logger()
    attempts = max_retries + 1
    response = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, timeout=timeout)
            break
        except requests.RequestException as exc:
            logger.warning(
                "Download attempt %s/%s for %s failed with network error: %s",
                attempt, attempts, url, exc,
            )
            if attempt == attempts:
                raise DownloadError(
                    f"network error after {attempts} attempts: {exc}"
                ) from exc

    if response.status_code != 200:
        raise DownloadError(
            f"unexpected HTTP status {response.status_code} for {url}"
        )

    content = response.content
    if not content:
        raise DownloadError(f"empty response body for {url}")

    if not zipfile.is_zipfile(io.BytesIO(content)):
        content_type = response.headers.get("Content-Type", "unknown")
        preview = content[:200]
        raise DownloadError(
            f"response for {url} is not a zip archive "
            f"(Content-Type={content_type}, first bytes={preview!r})"
        )

    archive = io.BytesIO(content)
    try:
        with zipfile.ZipFile(archive) as zip_ref:
            bad_entry = zip_ref.testzip()
            if bad_entry is not None:
                raise DownloadError(
                    f"corrupt zip archive for {url}, bad entry: {bad_entry}"
                )
    except zipfile.BadZipFile as exc:
        raise DownloadError(f"corrupt zip archive for {url}: {exc}") from exc

    archive.seek(0)
    logger.info("Verified zip download from %s (%s bytes)", url, len(content))
    return archive


@task(log_prints=True, name="Reading data as dataframe")
def read_csv(data: io.BytesIO) -> pd.DataFrame:
    """Extract the largest CSV contained in a (verified) zip archive."""
    try:
        with zipfile.ZipFile(data, "r") as zip_ref:
            csv_files = [
                name for name in zip_ref.namelist() if name.endswith(".csv")
            ]
            if not csv_files:
                raise ValueError(
                    "ZIP file does not contain a CSV file. "
                    "Expected at least one CSV file."
                )

            df_list = []
            for csv_file in csv_files:
                with zip_ref.open(csv_file) as f:
                    df_list.append(pd.read_csv(f, encoding="latin-1"))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"invalid zip archive: {exc}") from exc

    return max(df_list, key=len)


@task(log_prints=True, name="Writing to GCS bucket")
def write_gcs(df: pd.DataFrame, filename: str, bucket_name: str) -> None:
    """Upload a pandas DataFrame as a parquet file to GCS."""
    gcs_bucket = GcsBucket.load(GCS_BUCKET_BLOCK)
    gcs_bucket.upload_from_dataframe(
        df=df,
        to_path=filename,
        serialization_format="parquet_snappy",
        timeout=1000,
    )


@task(log_prints=True, name="Extracting from GCS bucket")
def extract_from_gcs(available_months: list[str]) -> pd.DataFrame:
    """Download and concatenate parquet files for the given months only.

    Blobs for months that failed upstream are deliberately absent from
    ``available_months``: a failed month must never be silently treated as
    empty data. Raises NoUsableDataError when nothing can be loaded.
    """
    logger = get_run_logger()
    gcs_block = GcsBucket.load(GCS_BUCKET_BLOCK)
    wanted_blobs = {gcs_blob_name(month) for month in available_months}

    frames = []
    for blob in gcs_block.list_blobs(GCS_PREFIX):
        if not blob.name.endswith(".parquet.snappy"):
            continue
        if blob.name not in wanted_blobs:
            logger.warning(
                "Ignoring GCS blob not part of this run's usable months: %s",
                blob.name,
            )
            continue
        logger.info("Loading %s from GCS", blob.name)
        frames.append(pd.read_parquet(f"gcs://{blob.bucket.name}/{blob.name}"))

    if not frames:
        raise NoUsableDataError(
            "no readable parquet files found in GCS for months: "
            + ", ".join(sorted(available_months))
        )

    return pd.concat(frames, ignore_index=True)


@task(log_prints=True, name="Transforming Data")
def transform(df: pd.DataFrame) -> pd.DataFrame:
    """Data cleaning and transformation"""
    df = df.astype({
        "ride_id": "str",
        "rideable_type": "str",
        "started_at": "datetime64[ns]",
        "ended_at": "datetime64[ns]",
        "start_station_name": "str",
        "start_station_id": "str",
        "end_station_name": "str",
        "end_station_id": "str",
        "start_lat": "float64",
        "start_lng": "float64",
        "end_lat": "float64",
        "end_lng": "float64",
        "member_casual": "str",
    })
    return df


@task(log_prints=True, name="Writing to BQ table")
def write_bq(df: pd.DataFrame) -> None:
    """Write DataFrame to BigQuery"""
    gcp_credentials_block = GcpCredentials.load(GCS_CREDENTIALS_BLOCK)

    df.to_gbq(
        destination_table=BQ_DESTINATION_TABLE,
        project_id=BQ_PROJECT_ID,
        credentials=gcp_credentials_block.get_credentials_from_service_account(),
        chunksize=500_000,
        if_exists="append",
    )


# I tried to trigger dbt job from prefect but got some error
# thats why I commented this part of the code and ran it using cli
# If you want to try it out for yourself uncomment the below block run this script
#
#
# @task(log_prints=True, name="Runs dbt to transform the data and derive columns")
# def trigger_dbt_flow() -> object:
#     """Triggers the dbt dependency and build commands"""
#
#     dbt_cli_profile = DbtCliProfile.load("divvy-dbt-cli-profile")
#
#     with DbtCoreOperation(
#         commands=["dbt deps", "dbt build --var 'is_test_run: false'"],
#         project_dir="~/Downloads/de-zoomcamp-final-project/dbt/divvy_project/",
#         profiles_dir="~/Downloads/de-zoomcamp-final-project/dbt/divvy_project/",
#         dbt_cli_profile=dbt_cli_profile,
#         overwrite_profiles=True,
#     ) as dbt_operation:
#         dbt_process = dbt_operation.trigger()
#         dbt_process.wait_for_completion()
#         result = dbt_process.fetch_result()
#     return result


# ---------------------------------------------------------------------------
# Per-month subflow and settlement reporting
# ---------------------------------------------------------------------------

@flow(log_prints=True, name="Process single month")
def process_month(month: str,
                  existing_blobs: set[str],
                  bucket_name: str = BUCKET_NAME) -> MonthResult:
    """Download, validate, parse and upload one month.

    Any failure is captured into a MonthResult with status='failed' instead of
    propagating, so one bad month can never stop the remaining months.
    """
    logger = get_run_logger()
    url = build_url(month)
    base_path = gcs_base_path(month)
    blob_name = gcs_blob_name(month)

    if is_month_uploaded(month, existing_blobs):
        logger.info(
            "Month %s already present in GCS (%s); skipping re-download",
            month, blob_name,
        )
        return MonthResult(
            month=month,
            status=STATUS_SKIPPED,
            url=url,
            gcs_blob=blob_name,
            reason=None,
        )

    try:
        data = download_file(url)
        df = read_csv(data)
        # base_path already ends with ".parquet"; the GcsBucket adds ".snappy".
        write_gcs(df, base_path, bucket_name)
    except DownloadError as exc:
        logger.error("Month %s failed at download/validation stage: %s",
                     month, exc)
        return MonthResult(
            month=month,
            status=STATUS_FAILED,
            url=url,
            gcs_blob=blob_name,
            reason=f"download: {exc}",
        )
    except Exception as exc:  # noqa: BLE001 - record any failure, keep going
        logger.exception("Month %s failed: %s", month, exc)
        return MonthResult(
            month=month,
            status=STATUS_FAILED,
            url=url,
            gcs_blob=blob_name,
            reason=f"{type(exc).__name__}: {exc}",
        )

    # Track the newly uploaded object so a later re-run in the same process
    # also skips it.
    existing_blobs.add(blob_name)
    logger.info("Month %s uploaded to GCS as %s", month, blob_name)
    return MonthResult(
        month=month,
        status=STATUS_SUCCESS,
        url=url,
        gcs_blob=blob_name,
        reason=None,
    )


def print_settlement(results: list[MonthResult]) -> None:
    """Log the auditable per-month settlement table."""
    logger = get_run_logger()
    succeeded = [r for r in results if r.status == STATUS_SUCCESS]
    skipped = [r for r in results if r.status == STATUS_SKIPPED]
    failed = [r for r in results if r.status == STATUS_FAILED]

    logger.info("=" * 78)
    logger.info("MONTHLY SETTLEMENT: %s months | %s success | %s skipped | %s failed",
                len(results), len(succeeded), len(skipped), len(failed))
    logger.info("-" * 78)
    logger.info("%-8s  %-8s  %s", "month", "status", "reason")
    # Results are built in month-ascending order; print them in that order.
    for result in results:
        logger.info("%-8s  %-8s  %s",
                    result.month, result.status, result.reason or "")
    logger.info("-" * 78)
    if failed:
        # Stable, ascending list so a re-run can target exactly these months.
        failed_months = sorted(result.month for result in failed)
        logger.info("Failed months (ascending, re-run targets): %s",
                    ", ".join(failed_months))
    else:
        logger.info("No failed months.")
    logger.info("=" * 78)


@flow(log_prints=True, name="web_to_gcs_to_bq")
def web_to_gcs_to_bq(start_month: str = START_MONTH,
                     end_month: str = END_MONTH,
                     bucket_name: str = BUCKET_NAME) -> dict:
    """End-to-end monthly ingestion, resilient to individual bad months."""
    logger = get_run_logger()
    months = iter_year_months(start_month, end_month)
    logger.info(
        "Pipeline covers %s months from %s to %s",
        len(months), months[0], months[-1],
    )

    try:
        existing_blobs = list_gcs_blobs(GCS_PREFIX)
    except Exception as exc:  # noqa: BLE001 - degrade to "all months new"
        logger.warning(
            "Could not list existing GCS blobs (%s); "
            "treating every month as a new upload",
            exc,
        )
        existing_blobs = set()

    # Months are processed strictly in ascending order; a failure is captured
    # inside process_month and never breaks this loop.
    results: list[MonthResult] = []
    for month in months:
        results.append(process_month(month, existing_blobs, bucket_name))

    print_settlement(results)

    failed = [r for r in results if r.status == STATUS_FAILED]
    available_months = [
        r.month for r in results
        if r.status in (STATUS_SUCCESS, STATUS_SKIPPED)
    ]

    settlement = {
        "start_month": start_month,
        "end_month": end_month,
        "summary": {
            "total": len(results),
            "success": sum(r.status == STATUS_SUCCESS for r in results),
            "skipped": sum(r.status == STATUS_SKIPPED for r in results),
            "failed": len(failed),
        },
        # Per-month, month-ascending records with month id, status and reason.
        "months": [result.to_dict() for result in results],
        # Sorted ascending for stable, targeted re-runs.
        "failed_months": sorted(result.month for result in failed),
        "available_months": available_months,
    }

    if not available_months:
        raise NoUsableDataError(
            "all "
            f"{len(failed)} month(s) failed; refusing to write an empty "
            "BigQuery table. Settlement: "
            + ", ".join(
                f"{r.month} ({r.reason})" for r in failed
            )
        )

    if failed:
        logger.warning(
            "Proceeding to BigQuery load with %s/%s usable months. "
            "Missing months: %s",
            len(available_months), len(months),
            ", ".join(settlement["failed_months"]),
        )

    df = extract_from_gcs(available_months)
    df = transform(df)
    write_bq(df)

    settlement["bq_rows_written"] = int(len(df))
    return settlement


if __name__ == "__main__":
    web_to_gcs_to_bq()
