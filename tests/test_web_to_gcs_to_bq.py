"""Offline tests for prefect/web_to_gcs_to_bq.py.

The real Prefect / prefect_gcp / google-cloud packages are not required here:
they are replaced with lightweight stubs before the module under test is
imported. No network calls or GCP operations are performed.

Run from the repository root with an isolated interpreter mode:

    python -I tests/test_web_to_gcs_to_bq.py
"""
import io
import sys
import types
import zipfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "prefect" / "web_to_gcs_to_bq.py"


# ---------------------------------------------------------------------------
# Stubs for third-party modules that are not installed in this environment
# ---------------------------------------------------------------------------

def _identity_decorator(*dec_args, **dec_kwargs):
    """Mimic @task/@flow with optional kwargs, preserving call semantics."""

    def wrap(fn):
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        wrapper.__wrapped__ = fn
        wrapper.__name__ = getattr(fn, "__name__", "wrapped")
        return wrapper

    # Support both @task and @task(...) / @flow(name="...")
    if len(dec_args) == 1 and callable(dec_args[0]) and not dec_kwargs:
        return wrap(dec_args[0])
    return wrap


class _FakeLogger:
    def info(self, *args, **kwargs):
        pass

    warning = info
    error = info

    def exception(self, *args, **kwargs):
        pass


_prefect_stub = types.ModuleType("prefect")
_prefect_stub.task = _identity_decorator
_prefect_stub.flow = _identity_decorator
_prefect_stub.get_run_logger = lambda: _FakeLogger()

_prefect_gcp_stub = types.ModuleType("prefect_gcp")


class _FakeGcpCredentials:
    def __init__(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls, name):
        return cls()

    def get_credentials_from_service_account(self):
        return object()


_prefect_gcp_stub.GcpCredentials = _FakeGcpCredentials

_cloud_storage_stub = types.ModuleType("prefect_gcp.cloud_storage")


class _FakeBlob:
    def __init__(self, name, bucket_name):
        self.name = name
        self.bucket = types.SimpleNamespace(name=bucket_name)


class _FakeGcsBucket:
    bucket_name = "fake-bucket"
    blobs: set[str] = set()
    uploads: list[tuple] = []

    def __init__(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls, name):
        return cls()

    def list_blobs(self, prefix):
        return [_FakeBlob(name, self.bucket_name)
                for name in sorted(_FakeGcsBucket.blobs)
                if name.startswith(prefix)]

    def upload_from_dataframe(self, df, to_path,
                              serialization_format=None, timeout=None):
        # Mirror prefect_gcp behaviour: parquet_snappy appends ".snappy".
        suffix = ".snappy" if serialization_format == "parquet_snappy" else ""
        _FakeGcsBucket.blobs.add(to_path + suffix)
        _FakeGcsBucket.uploads.append((to_path, serialization_format))


_cloud_storage_stub.GcsBucket = _FakeGcsBucket
_prefect_gcp_stub.cloud_storage = _cloud_storage_stub

sys.modules["prefect"] = _prefect_stub
sys.modules["prefect_gcp"] = _prefect_gcp_stub
sys.modules["prefect_gcp.cloud_storage"] = _cloud_storage_stub

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "web_to_gcs_to_bq_under_test", MODULE_PATH
)
pipe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pipe)

import pandas as pd  # noqa: E402
import requests  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_zip_bytes(csv_name: str = "trips.csv",
                   csv_content: str = "ride_id\nR1\nR2\n",
                   corrupt: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(csv_name, csv_content)
    raw = buffer.getvalue()
    if corrupt:
        raw = raw[:-20] + b"\x00" * 20
    return raw


class FakeResponse:
    def __init__(self, status_code=200, content=b"",
                 headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {"Content-Type": "application/zip"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class PureHelperTests(unittest.TestCase):
    def test_iter_year_months_full_range_is_33_ascending(self):
        months = pipe.iter_year_months("202004", "202212")
        self.assertEqual(len(months), 33)
        self.assertEqual(months[0], "202004")
        self.assertEqual(months[-1], "202212")
        self.assertEqual(months, sorted(months))
        # Year boundary is handled correctly (no 202013).
        self.assertIn("202012", months)
        self.assertIn("202101", months)
        self.assertEqual(
            months.index("202101") - months.index("202012"), 1
        )

    def test_iter_year_months_single_month(self):
        self.assertEqual(pipe.iter_year_months("202106", "202106"),
                         ["202106"])

    def test_iter_year_months_rejects_bad_bounds(self):
        with self.assertRaises(ValueError):
            pipe.iter_year_months("202113", "202112")
        with self.assertRaises(ValueError):
            pipe.iter_year_months("202201", "202112")
        with self.assertRaises(ValueError):
            pipe.iter_year_months("abcd", "202112")

    def test_build_url_and_gcs_paths(self):
        self.assertEqual(
            pipe.build_url("202107"),
            "https://divvy-tripdata.s3.amazonaws.com/"
            "202107-divvy-tripdata.zip",
        )
        self.assertEqual(
            pipe.gcs_base_path("202107"),
            "divvy-tripdata/202107-divvy-tripdata.parquet",
        )
        self.assertEqual(
            pipe.gcs_blob_name("202107"),
            "divvy-tripdata/202107-divvy-tripdata.parquet.snappy",
        )


class DownloadValidationTests(unittest.TestCase):
    def test_non_200_is_failed_without_raising_from_get(self):
        with mock.patch.object(
            pipe.requests, "get",
            return_value=FakeResponse(status_code=404,
                                      content=b"<Error>Not Found</Error>",
                                      headers={"Content-Type": "text/html"}),
        ):
            with self.assertRaises(pipe.DownloadError) as ctx:
                pipe.download_file("https://example.com/202101.zip",
                                   max_retries=0)
        self.assertIn("404", str(ctx.exception))

    def test_200_html_error_page_is_detected_as_non_zip(self):
        with mock.patch.object(
            pipe.requests, "get",
            return_value=FakeResponse(
                status_code=200,
                content=b"<html><body>Error page</body></html>",
                headers={"Content-Type": "text/html"},
            ),
        ):
            with self.assertRaises(pipe.DownloadError) as ctx:
                pipe.download_file("https://example.com/202102.zip",
                                   max_retries=0)
        self.assertIn("not a zip", str(ctx.exception))

    def test_empty_body_is_failed(self):
        with mock.patch.object(
            pipe.requests, "get",
            return_value=FakeResponse(200, b""),
        ):
            with self.assertRaises(pipe.DownloadError):
                pipe.download_file("https://example.com/empty.zip",
                                   max_retries=0)

    def test_corrupt_zip_is_failed(self):
        with mock.patch.object(
            pipe.requests, "get",
            return_value=FakeResponse(200, make_zip_bytes(corrupt=True)),
        ):
            with self.assertRaises(pipe.DownloadError):
                pipe.download_file("https://example.com/corrupt.zip",
                                   max_retries=0)

    def test_valid_zip_returns_seekable_bytesio(self):
        raw = make_zip_bytes()
        with mock.patch.object(
            pipe.requests, "get",
            return_value=FakeResponse(200, raw),
        ):
            data = pipe.download_file("https://example.com/good.zip",
                                      max_retries=0)
        self.assertIsInstance(data, io.BytesIO)
        with zipfile.ZipFile(data) as zf:
            self.assertEqual(zf.namelist(), ["trips.csv"])

    def test_network_error_retried_then_raised(self):
        calls = {"n": 0}

        def flaky(url, timeout=None):
            calls["n"] += 1
            raise requests.ConnectionError("reset by peer")

        with mock.patch.object(pipe.requests, "get", side_effect=flaky):
            with self.assertRaises(pipe.DownloadError) as ctx:
                pipe.download_file("https://example.com/flaky.zip",
                                   max_retries=2)
        self.assertEqual(calls["n"], 3)
        self.assertIn("network error", str(ctx.exception))


class ReadCsvTests(unittest.TestCase):
    def test_reads_largest_csv_and_handles_multiple(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("small.csv", "ride_id\nA\n")
            zf.writestr("big.csv", "ride_id\nA\nB\nC\n")
        buffer.seek(0)
        df = pipe.read_csv(buffer)
        self.assertEqual(len(df), 3)

    def test_zip_without_csv_raises(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("notes.txt", "no csv here")
        buffer.seek(0)
        with self.assertRaises(ValueError):
            pipe.read_csv(buffer)


class ProcessMonthTests(unittest.TestCase):
    def setUp(self):
        _FakeGcsBucket.blobs = set()
        _FakeGcsBucket.uploads = []

    def test_existing_blob_base_path_is_skipped_no_download(self):
        existing = {pipe.gcs_base_path("202101")}
        with mock.patch.object(pipe.requests, "get") as get_mock:
            result = pipe.process_month("202101", existing)
        get_mock.assert_not_called()
        self.assertEqual(result.status, pipe.STATUS_SKIPPED)
        self.assertEqual(result.month, "202101")
        self.assertIsNone(result.reason)
        self.assertEqual(result.gcs_blob, pipe.gcs_blob_name("202101"))

    def test_existing_blob_with_snappy_suffix_is_skipped(self):
        # The real GCS listing returns full object names including ".snappy".
        existing = {pipe.gcs_blob_name("202101")}
        with mock.patch.object(pipe.requests, "get") as get_mock:
            result = pipe.process_month("202101", existing)
        get_mock.assert_not_called()
        self.assertEqual(result.status, pipe.STATUS_SKIPPED)

    def test_bad_month_is_failed_and_other_state_untouched(self):
        existing = set()
        with mock.patch.object(
            pipe.requests, "get",
            return_value=FakeResponse(
                403, b"forbidden",
                headers={"Content-Type": "text/html"}),
        ):
            result = pipe.process_month("202102", existing)
        self.assertEqual(result.status, pipe.STATUS_FAILED)
        self.assertIn("download", result.reason)
        self.assertIn("403", result.reason)
        # Nothing uploaded, existing set unchanged.
        self.assertEqual(_FakeGcsBucket.uploads, [])
        self.assertEqual(existing, set())

    def test_good_month_is_success_and_registered(self):
        existing = set()
        with mock.patch.object(
            pipe.requests, "get",
            return_value=FakeResponse(200, make_zip_bytes()),
        ):
            result = pipe.process_month("202103", existing)
        self.assertEqual(result.status, pipe.STATUS_SUCCESS)
        self.assertIsNone(result.reason)
        self.assertEqual(len(_FakeGcsBucket.uploads), 1)
        # No doubled ".parquet.parquet" suffix.
        self.assertEqual(
            _FakeGcsBucket.uploads[0][0], pipe.gcs_base_path("202103")
        )
        self.assertIn(pipe.gcs_blob_name("202103"), existing)

    def test_corrupt_archive_mid_pipeline_recorded_not_raised(self):
        existing = set()
        with mock.patch.object(
            pipe.requests, "get",
            return_value=FakeResponse(200, make_zip_bytes(corrupt=True)),
        ):
            result = pipe.process_month("202104", existing)
        self.assertEqual(result.status, pipe.STATUS_FAILED)
        self.assertIn("download", result.reason)

    def test_upload_error_recorded_not_raised(self):
        existing = set()

        with mock.patch.object(
            pipe.requests, "get",
            return_value=FakeResponse(200, make_zip_bytes()),
        ), mock.patch.object(
            pipe, "write_gcs",
            side_effect=RuntimeError("GCS unavailable"),
        ):
            result = pipe.process_month("202105", existing)
        self.assertEqual(result.status, pipe.STATUS_FAILED)
        self.assertIn("RuntimeError", result.reason)
        self.assertIn("GCS unavailable", result.reason)


class FlowResilienceTests(unittest.TestCase):
    def setUp(self):
        _FakeGcsBucket.blobs = set()
        _FakeGcsBucket.uploads = []

    def _patch_months(self, good, bad, skipped):
        """Build a requests.get whose response depends on the month."""
        def fake_get(url, timeout=None):
            month = url.split("/")[-1][:6]
            if month in skipped:
                raise AssertionError(f"skipped month {month} was downloaded")
            if month in bad:
                return FakeResponse(
                    404, b"<Error/>",
                    headers={"Content-Type": "text/html"})
            return FakeResponse(200, make_zip_bytes())

        return mock.patch.object(pipe.requests, "get", side_effect=fake_get)

    def test_mixed_skips_successes_failures_all_settled(self):
        skipped_months = {"202004"}
        bad_months = {"202005", "202007"}
        all_months = ["202004", "202005", "202006", "202007", "202008"]

        # Pre-populate GCS with the month that should be skipped.
        _FakeGcsBucket.blobs = {pipe.gcs_blob_name(m) for m in skipped_months}

        captured = {}

        def fake_extract(available_months):
            captured["available_months"] = list(available_months)
            return pd.DataFrame({"ride_id": ["x"] * len(available_months)})

        with self._patch_months(set(), bad_months, skipped_months), \
                mock.patch.object(pipe, "extract_from_gcs",
                                  side_effect=fake_extract), \
                mock.patch.object(pipe, "transform",
                                  side_effect=lambda df: df), \
                mock.patch.object(pipe, "write_bq") as write_bq_mock:
            settlement = pipe.web_to_gcs_to_bq(
                start_month="202004", end_month="202008",
            )

        # The loop never stopped: all five months settled.
        self.assertEqual(settlement["summary"]["total"], 5)
        self.assertEqual(settlement["summary"]["skipped"], 1)
        self.assertEqual(settlement["summary"]["failed"], 2)
        self.assertEqual(settlement["summary"]["success"], 2)

        # Month records are in ascending order and carry status + reason.
        month_records = settlement["months"]
        self.assertEqual([r["month"] for r in month_records], all_months)
        by_month = {r["month"]: r for r in month_records}
        self.assertEqual(by_month["202004"]["status"],
                         pipe.STATUS_SKIPPED)
        self.assertEqual(by_month["202005"]["status"], pipe.STATUS_FAILED)
        self.assertIn("404", by_month["202005"]["reason"])
        self.assertEqual(by_month["202006"]["status"],
                         pipe.STATUS_SUCCESS)

        # Failed months sorted ascending for stable re-runs.
        self.assertEqual(settlement["failed_months"],
                         ["202005", "202007"])

        # The merge only sees usable months; failed months are not treated
        # as empty data.
        self.assertEqual(
            captured["available_months"],
            ["202004", "202006", "202008"],
        )
        write_bq_mock.assert_called_once()

    def test_all_months_failed_raises_instead_of_empty_bq_write(self):
        bad = {"202004", "202005"}
        with self._patch_months(set(), bad, set()), \
                mock.patch.object(pipe, "extract_from_gcs") as extract_mock, \
                mock.patch.object(pipe, "write_bq") as write_bq_mock:
            with self.assertRaises(pipe.NoUsableDataError) as ctx:
                pipe.web_to_gcs_to_bq("202004", "202005")

        extract_mock.assert_not_called()
        write_bq_mock.assert_not_called()
        message = str(ctx.exception)
        self.assertIn("202004", message)
        self.assertIn("202005", message)
        self.assertIn("refusing to write an empty", message)

    def test_rerun_after_failures_only_downloads_missing_months(self):
        # First run: 202005 fails, 202004 succeeds.
        _FakeGcsBucket.blobs = set()
        with self._patch_months(set(), {"202005"}, set()), \
                mock.patch.object(
                    pipe, "extract_from_gcs",
                    return_value=pd.DataFrame({"ride_id": ["x"]})), \
                mock.patch.object(pipe, "transform",
                                  side_effect=lambda df: df), \
                mock.patch.object(pipe, "write_bq"):
            first = pipe.web_to_gcs_to_bq("202004", "202005")
        self.assertEqual(first["failed_months"], ["202005"])
        self.assertEqual(first["summary"]["success"], 1)

        # Re-run: previously successful month is now skipped, only the
        # missing month is downloaded.
        downloaded = []

        def fake_get(url, timeout=None):
            month = url.split("/")[-1][:6]
            downloaded.append(month)
            if month == "202005":
                return FakeResponse(200, make_zip_bytes())
            raise AssertionError(f"unexpected download for {month}")

        with mock.patch.object(pipe.requests, "get", side_effect=fake_get), \
                mock.patch.object(pipe, "extract_from_gcs",
                                  return_value=pd.DataFrame(
                                      {"ride_id": ["x", "y"]})), \
                mock.patch.object(pipe, "transform",
                                  side_effect=lambda df: df), \
                mock.patch.object(pipe, "write_bq"):
            second = pipe.web_to_gcs_to_bq("202004", "202005")

        self.assertEqual(downloaded, ["202005"])
        self.assertEqual(second["summary"]["skipped"], 1)
        self.assertEqual(second["summary"]["success"], 1)
        self.assertEqual(second["summary"]["failed"], 0)
        self.assertEqual(second["failed_months"], [])


class ExtractGuardTests(unittest.TestCase):
    def setUp(self):
        _FakeGcsBucket.blobs = set()

    def test_ignores_blobs_belonging_to_failed_or_other_months(self):
        _FakeGcsBucket.blobs = {
            pipe.gcs_blob_name("202004"),
            "divvy-tripdata/199901-divvy-tripdata.parquet.snappy",
            "divvy-tripdata/readme.txt",
        }
        with mock.patch.object(pd, "read_parquet",
                               return_value=pd.DataFrame({"ride_id": ["a"]})):
            df = pipe.extract_from_gcs(["202004"])
        self.assertEqual(len(df), 1)

    def test_no_matching_blobs_raises(self):
        _FakeGcsBucket.blobs = {
            "divvy-tripdata/199901-divvy-tripdata.parquet.snappy",
        }
        with self.assertRaises(pipe.NoUsableDataError):
            pipe.extract_from_gcs(["202004"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
