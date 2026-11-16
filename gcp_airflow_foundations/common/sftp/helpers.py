"""Reusable helpers for loading files from an SFTP server into GCS.

An SFTP ingestion run is settled file by file: every file is downloaded,
uploaded and recorded independently of its neighbours. A failure for one file
does not prevent the remaining files from being processed; the local staging
area is always cleaned up; and the outcome of the run is published as a single
XCom entry (``INGESTION_SUMMARY_XCOM_KEY``) listing the uploaded files, the
files skipped because they already exist in GCS for the same ``ds``, and the
files that failed together with the reason for each failure.

The task itself fails at the end of the run when at least one file failed.
"""

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from airflow.exceptions import AirflowException
from airflow.models import Variable

# XCom key holding the per-file summary of an ingestion task run.
INGESTION_SUMMARY_XCOM_KEY = "sftp_ingestion_summary"

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# Local directory (relative to the Airflow working directory) used for downloads.
DEFAULT_STAGING_DIR = "sftp_data"

_MAX_REASON_LENGTH = 500
_MAX_REASONS_IN_MESSAGE = 10


def save_id_to_file(private_key_var):
    key = Variable.get(private_key_var)
    secret_path = "id_rsa"
    with open(secret_path, "w") as f:
        f.write(key)


def remove_private_key_file(secret_path="id_rsa"):
    """Remove a previously materialised private key file, ignoring errors.

    Removal is deliberately best effort so that it can be used from a
    ``finally`` block without masking an ingestion error.
    """
    try:
        if os.path.isfile(secret_path):
            os.remove(secret_path)
    except OSError as exc:
        logging.warning("Could not remove private key file %s: %s", secret_path, exc)


def create_local_staging_dir(
    table_name=None, ds=None, base_dir=DEFAULT_STAGING_DIR
) -> str:
    """Create a unique local directory for a single ingestion task run.

    A unique directory per run means cleanup can never remove files that are
    still being handled by a concurrent run, and files left behind by a failed
    run can never be mistaken for freshly downloaded ones.
    """
    if not os.path.isdir(base_dir):
        os.makedirs(base_dir, exist_ok=True)

    prefix_parts = [str(part) for part in (table_name, ds) if part not in (None, "")]
    prefix = "_".join(prefix_parts)
    prefix = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in prefix)
    prefix = prefix[:60]
    if prefix:
        prefix += "_"

    staging_dir = tempfile.mkdtemp(prefix=prefix, dir=base_dir)
    try:
        os.chmod(staging_dir, 0o777)
    except OSError:
        pass
    return staging_dir


def ensure_directory(directory):
    """Create ``directory`` (and parents) if it does not exist yet."""
    if not directory or os.path.isdir(directory):
        return
    os.makedirs(directory, exist_ok=True)
    try:
        os.chmod(directory, 0o777)
    except OSError:
        pass


def cleanup_local_file(path):
    """Remove a single local file if present, ignoring errors."""
    if not path:
        return
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as exc:
        logging.warning("Could not remove local file %s: %s", path, exc)


def cleanup_local_path(path):
    """Best effort removal of a staging directory (or file).

    This never raises: it is meant to be called from a ``finally`` block so a
    failed file transfer cannot prevent cleanup. Only the given run directory
    is removed, never the whole staging root when another run still uses it.
    """
    if not path:
        return
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logging.warning("Could not clean up local staging path %s: %s", path, exc)
        return

    parent = os.path.dirname(path)
    if parent and os.path.basename(parent) == DEFAULT_STAGING_DIR:
        # Succeeds only when no other run is currently using the staging root.
        try:
            os.rmdir(parent)
        except OSError:
            pass


def describe_exception(exc, max_length=_MAX_REASON_LENGTH) -> str:
    """Render an exception as a single readable reason string."""
    message = str(exc).strip()
    description = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    return _truncate(description, max_length)


def _truncate(value, max_length=_MAX_REASON_LENGTH) -> str:
    text = str(value)
    if len(text) > max_length:
        text = text[: max_length - 3] + "..."
    return text


def gcs_object_exists(gcs_hook, bucket_name, object_name) -> bool:
    """Return True when ``object_name`` already exists in ``bucket_name``.

    The check is best effort: when GCS cannot be queried the object is treated
    as missing so ingestion can still make progress (re-uploading identical
    content is harmless) and a warning is logged.
    """
    try:
        exists_method = getattr(gcs_hook, "exists", None)
        if callable(exists_method):
            return bool(
                exists_method(bucket_name=bucket_name, object_name=object_name)
            )
        matching_objects = gcs_hook.list(
            bucket_name=bucket_name, prefix=object_name
        ) or []
        for match in matching_objects:
            if isinstance(match, dict):
                match = match.get("name") or match.get("object_name")
            if match == object_name:
                return True
        return False
    except Exception as exc:
        logging.warning(
            "Could not check whether gs://%s/%s already exists (%s); "
            "the file will be uploaded again.",
            bucket_name,
            object_name,
            exc,
        )
        return False


@dataclass
class FileIngestionResult:
    """Outcome for a single remote file."""

    remote_path: str
    status: str
    destination: Optional[str] = None
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        result = {"file": self.remote_path, "status": self.status}
        if self.destination is not None:
            result["gcs_object"] = self.destination
        if self.reason is not None:
            result["reason"] = self.reason
        return result


class IngestionSummary:
    """Collects the per-file outcome of one ingestion task run."""

    def __init__(self, table_name=None, ds=None):
        self.table_name = table_name
        self.ds = ds
        self.results: List[FileIngestionResult] = []
        # A failure that is not attributable to a single file (e.g. the SFTP
        # connection or the directory listing failed before the loop started).
        self.run_error: Optional[str] = None

    def _record(self, remote_path, status, destination=None, reason=None):
        self.results.append(
            FileIngestionResult(
                remote_path=remote_path,
                status=status,
                destination=destination,
                reason=reason,
            )
        )

    def record_success(self, remote_path, destination=None):
        self._record(remote_path, STATUS_SUCCESS, destination)

    def record_skip(self, remote_path, destination=None, reason="already uploaded"):
        self._record(remote_path, STATUS_SKIPPED, destination, reason)

    def record_failure(self, remote_path, reason, destination=None):
        self._record(
            remote_path,
            STATUS_FAILED,
            destination,
            _truncate(reason or "unknown error"),
        )

    def record_run_error(self, error):
        """Record a failure that cannot be attributed to a single file."""
        if isinstance(error, BaseException):
            self.run_error = describe_exception(error)
        else:
            self.run_error = _truncate(error)

    def by_status(self, status) -> List[FileIngestionResult]:
        return [result for result in self.results if result.status == status]

    @property
    def uploaded(self) -> List[FileIngestionResult]:
        return self.by_status(STATUS_SUCCESS)

    @property
    def skipped(self) -> List[FileIngestionResult]:
        return self.by_status(STATUS_SKIPPED)

    @property
    def failed(self) -> List[FileIngestionResult]:
        return self.by_status(STATUS_FAILED)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def has_failures(self) -> bool:
        return bool(self.failed) or self.run_error is not None

    @property
    def status(self) -> str:
        return STATUS_FAILED if self.has_failures else STATUS_SUCCESS

    def message(self) -> str:
        description = "SFTP ingestion"
        if self.table_name is not None:
            description += f" for table '{self.table_name}'"
        if self.ds is not None:
            description += f" (ds={self.ds})"
        return (
            f"{description}: {self.total} file(s) handled, "
            f"{len(self.uploaded)} uploaded, {len(self.skipped)} skipped, "
            f"{len(self.failed)} failed."
        )

    def failure_message(self, xcom_key=INGESTION_SUMMARY_XCOM_KEY) -> str:
        parts = [self.message()]
        failures = self.failed[:_MAX_REASONS_IN_MESSAGE]
        if failures:
            details = "; ".join(
                f"{result.remote_path}: {result.reason}" for result in failures
            )
            remaining = len(self.failed) - len(failures)
            if remaining > 0:
                details += f"; and {remaining} more failed file(s)"
            parts.append(f"Failed file(s): {details}.")
        if self.run_error:
            parts.append(f"The run failed before all files could be handled: {self.run_error}.")
        parts.append(f"See the '{xcom_key}' XCom for the full per-file summary.")
        return " ".join(parts)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "table_name": self.table_name,
            "ds": self.ds,
            "status": self.status,
            "run_error": self.run_error,
            "total_files": self.total,
            "uploaded_count": len(self.uploaded),
            "skipped_count": len(self.skipped),
            "failed_count": len(self.failed),
            "uploaded_files": [result.as_dict() for result in self.uploaded],
            "skipped_files": [result.as_dict() for result in self.skipped],
            "failed_files": [result.as_dict() for result in self.failed],
            "message": self.message(),
        }


@dataclass
class FileTransfer:
    """Describes one remote file to move from SFTP into GCS.

    Attributes:
        remote_path: path of the file on the SFTP server
        local_path: local path to download the file to
        object_name: destination object name inside the GCS bucket (``None``
            for files that are never uploaded, e.g. a flag file)
        skip_reason: when set the file is not transferred at all (e.g. a flag
            file), but is still recorded in the summary
    """

    remote_path: str
    local_path: str
    object_name: Optional[str]
    skip_reason: Optional[str] = None


def transfer_file(
    sftp_hook, remote_path, local_path, gcs_hook, bucket_name, object_name
):
    """Download one file from SFTP, upload it to GCS and drop the local copy.

    The local copy is removed even when the transfer fails, so a failed file
    never leaves a partial download behind. Any exception is left to the caller
    so the failure can be attributed to this file only.
    """
    try:
        ensure_directory(os.path.dirname(local_path))
        sftp_hook.retrieve_file(remote_path, local_path)
        gcs_hook.upload(
            bucket_name=bucket_name, object_name=object_name, filename=local_path
        )
    finally:
        cleanup_local_file(local_path)


def transfer_files(
    files: List[FileTransfer],
    sftp_hook,
    gcs_hook,
    bucket_name: str,
    summary: IngestionSummary,
) -> IngestionSummary:
    """Transfer every file independently, recording the outcome of each one.

    Files whose destination object already exists in GCS (i.e. an earlier run
    with the same ``ds`` uploaded them) are skipped instead of uploaded again.
    """
    for transfer in files:
        if transfer.skip_reason:
            summary.record_skip(
                transfer.remote_path, transfer.object_name, transfer.skip_reason
            )
            continue

        if gcs_object_exists(gcs_hook, bucket_name, transfer.object_name):
            summary.record_skip(
                transfer.remote_path,
                transfer.object_name,
                (
                    f"already present at gs://{bucket_name}/{transfer.object_name}; "
                    "skipped to avoid a duplicate upload"
                ),
            )
            continue

        try:
            transfer_file(
                sftp_hook=sftp_hook,
                remote_path=transfer.remote_path,
                local_path=transfer.local_path,
                gcs_hook=gcs_hook,
                bucket_name=bucket_name,
                object_name=transfer.object_name,
            )
        except Exception as exc:
            reason = describe_exception(exc)
            logging.warning(
                "Failed to load %s into gs://%s/%s: %s",
                transfer.remote_path,
                bucket_name,
                transfer.object_name,
                reason,
            )
            summary.record_failure(
                transfer.remote_path, reason, transfer.object_name
            )
        else:
            logging.info(
                "Loaded %s into gs://%s/%s",
                transfer.remote_path,
                bucket_name,
                transfer.object_name,
            )
            summary.record_success(transfer.remote_path, transfer.object_name)

    return summary


def push_ingestion_summary(
    ti, summary: IngestionSummary, xcom_key=INGESTION_SUMMARY_XCOM_KEY
) -> Dict[str, Any]:
    """Publish the run summary to XCom (and the task log)."""
    payload = summary.as_dict() if isinstance(summary, IngestionSummary) else summary
    logging.info(
        "SFTP ingestion summary (%s): %s", xcom_key, json.dumps(payload, default=str)
    )
    if ti is not None:
        ti.xcom_push(key=xcom_key, value=payload)
    return payload


def finalize_ingestion(
    ti, summary: IngestionSummary, xcom_key=INGESTION_SUMMARY_XCOM_KEY
) -> Dict[str, Any]:
    """Publish the summary and fail the task when any file failed.

    The summary is pushed before the exception is raised so the run's outcome
    (uploaded, skipped and failed files with reasons) is always available. When
    no file was transferred because of a run-level error, the task fails too.
    """
    payload = push_ingestion_summary(ti, summary, xcom_key=xcom_key)
    if summary.has_failures:
        message = summary.failure_message(xcom_key=xcom_key)
        logging.error(message)
        raise AirflowException(message)
    logging.info(summary.message())
    return payload
