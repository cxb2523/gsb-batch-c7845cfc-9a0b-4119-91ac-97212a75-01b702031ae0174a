"""Unit tests for the file-by-file settlement of SFTP -> GCS ingestion.

These tests exercise the reusable helpers in
``gcp_airflow_foundations.common.sftp.helpers`` and the two ingestion
callables of ``SFTPFileIngestionDagBuilder`` with fake SFTP/GCS hooks, so no
network access is required.
"""

import os
import sys
import unittest
from types import SimpleNamespace

from airflow.exceptions import AirflowException

from gcp_airflow_foundations.common.sftp import helpers
from gcp_airflow_foundations.source_class import sftp_source


class FakeTaskInstance:
    def __init__(self):
        self.pushed = {}

    def xcom_push(self, key, value):
        self.pushed[key] = value

    def xcom_pull(self, key, task_ids=None):
        return self.pushed.get(key)


class FakeSFTPHook:
    def __init__(self, tree=None, failing=(), contents=None):
        self.tree = tree or []
        self.failing = set(failing)
        self.contents = contents or {}
        self.retrieved = []

    def get_tree_map(self, prefix):
        return self.tree

    def list_directory(self, path):
        return self.tree

    def retrieve_file(self, remote_path, local_path):
        self.retrieved.append(remote_path)
        if remote_path in self.failing:
            raise PermissionError(f"cannot read {remote_path}")
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "w") as handle:
            handle.write(self.contents.get(remote_path, "data"))


class FakeGCSHook:
    def __init__(self, existing=(), failing=()):
        self.existing = set(existing)
        self.failing = set(failing)
        self.uploaded = []

    def exists(self, bucket_name, object_name):
        return object_name in self.existing

    def upload(self, bucket_name, object_name, filename):
        if object_name in self.failing:
            raise ValueError(f"upload rejected for {object_name}")
        assert os.path.isfile(filename)
        self.uploaded.append(object_name)


class FakeTableConfig:
    def __init__(self, table_name="table", extra_options=None):
        self.table_name = table_name
        self.extra_options = extra_options or {
            "file_table_config": {"directory_prefix": "/remote"},
            "sftp_table_config": {},
        }


def make_builder(file_source_config=None, sftp_source_config=None):
    builder = sftp_source.SFTPFileIngestionDagBuilder.__new__(
        sftp_source.SFTPFileIngestionDagBuilder
    )
    builder.config = SimpleNamespace(
        source=SimpleNamespace(
            extra_options={
                "file_source_config": file_source_config
                or {"file_prefix_filtering": False, "airflow_date_template": "ds"},
                "sftp_source_config": sftp_source_config
                or {"sftp_connection_name": "sftp_default"},
            }
        )
    )
    return builder


class TestHelpers(unittest.TestCase):
    def test_summary_groups_results(self):
        summary = helpers.IngestionSummary(table_name="t", ds="2024-01-01")
        summary.record_success("a.csv", "t/2024-01-01/a.csv")
        summary.record_skip("b.csv", "t/2024-01-01/b.csv")
        summary.record_failure("c.csv", "boom")

        assert summary.total == 3
        assert len(summary.uploaded) == 1
        assert len(summary.skipped) == 1
        assert len(summary.failed) == 1
        assert summary.has_failures
        assert summary.status == helpers.STATUS_FAILED

        payload = summary.as_dict()
        assert payload["uploaded_count"] == 1
        assert payload["skipped_count"] == 1
        assert payload["failed_count"] == 1
        assert payload["failed_files"][0]["reason"] == "boom"
        assert payload["uploaded_files"][0]["file"] == "a.csv"

    def test_no_failures_means_success(self):
        summary = helpers.IngestionSummary(table_name="t", ds="2024-01-01")
        summary.record_success("a.csv")
        assert not summary.has_failures
        assert summary.status == helpers.STATUS_SUCCESS

    def test_run_error_is_a_failure(self):
        summary = helpers.IngestionSummary(table_name="t", ds="2024-01-01")
        summary.record_run_error(ConnectionError("sftp unreachable"))
        assert summary.has_failures
        assert summary.status == helpers.STATUS_FAILED
        payload = summary.as_dict()
        assert "ConnectionError: sftp unreachable" == payload["run_error"]
        message = summary.failure_message()
        assert "before all files could be handled" in message
        assert "sftp unreachable" in message

    def test_failure_message_lists_files_and_reasons(self):
        summary = helpers.IngestionSummary(table_name="t", ds="2024-01-01")
        summary.record_failure("a.csv", "PermissionError: nope")
        message = summary.failure_message()
        assert "a.csv" in message
        assert "PermissionError: nope" in message
        assert helpers.INGESTION_SUMMARY_XCOM_KEY in message

    def test_failure_message_is_bounded(self):
        summary = helpers.IngestionSummary(table_name="t", ds="2024-01-01")
        for index in range(helpers._MAX_REASONS_IN_MESSAGE + 5):
            summary.record_failure(f"f{index}.csv", "err")
        message = summary.failure_message()
        assert "and 5 more failed file(s)" in message

    def test_finalize_pushes_summary_then_raises(self):
        ti = FakeTaskInstance()
        summary = helpers.IngestionSummary(table_name="t", ds="2024-01-01")
        summary.record_failure("a.csv", "boom")

        with self.assertRaises(AirflowException):
            helpers.finalize_ingestion(ti, summary)

        pushed = ti.pushed[helpers.INGESTION_SUMMARY_XCOM_KEY]
        assert pushed["failed_count"] == 1
        assert pushed["failed_files"][0]["file"] == "a.csv"

    def test_finalize_without_failures_does_not_raise(self):
        ti = FakeTaskInstance()
        summary = helpers.IngestionSummary(table_name="t", ds="2024-01-01")
        summary.record_success("a.csv")
        payload = helpers.finalize_ingestion(ti, summary)
        assert payload["status"] == helpers.STATUS_SUCCESS
        assert ti.pushed[helpers.INGESTION_SUMMARY_XCOM_KEY]["uploaded_count"] == 1

    def test_gcs_object_exists_uses_exists_then_list(self):
        gcs_hook = FakeGCSHook(existing={"t/a.csv"})
        assert helpers.gcs_object_exists(gcs_hook, "bucket", "t/a.csv")
        assert not helpers.gcs_object_exists(gcs_hook, "bucket", "t/b.csv")

    def test_gcs_object_exists_is_best_effort(self):
        class BrokenHook:
            def exists(self, bucket_name, object_name):
                raise RuntimeError("api down")

        assert not helpers.gcs_object_exists(BrokenHook(), "bucket", "t/a.csv")

    def test_gcs_object_exists_falls_back_to_list(self):
        class ListOnlyHook:
            def __init__(self, matches):
                self.matches = matches

            def list(self, bucket_name, prefix):
                return self.matches

        assert helpers.gcs_object_exists(
            ListOnlyHook(["t/a.csv"]), "bucket", "t/a.csv"
        )
        assert helpers.gcs_object_exists(
            ListOnlyHook([{"name": "t/a.csv"}]), "bucket", "t/a.csv"
        )
        # A prefix match that is not the exact object is not enough.
        assert not helpers.gcs_object_exists(
            ListOnlyHook(["t/a.csv.backup"]), "bucket", "t/a.csv"
        )

    def test_staging_dir_is_unique_and_cleanup_is_scoped(self):
        first = helpers.create_local_staging_dir(table_name="t", ds="2024-01-01")
        second = helpers.create_local_staging_dir(table_name="t", ds="2024-01-01")
        try:
            assert first != second
            assert os.path.isdir(first) and os.path.isdir(second)
            with open(os.path.join(second, "keep.txt"), "w") as handle:
                handle.write("x")
            helpers.cleanup_local_path(first)
            assert not os.path.exists(first)
            # Cleaning one run must not remove files of another run.
            assert os.path.isfile(os.path.join(second, "keep.txt"))
        finally:
            helpers.cleanup_local_path(first)
            helpers.cleanup_local_path(second)

    def test_cleanup_never_raises(self):
        helpers.cleanup_local_path(None)
        helpers.cleanup_local_path("does/not/exist")
        helpers.remove_private_key_file("does/not/exist")

    def test_transfer_files_isolates_failures(self):
        ti_dir = helpers.create_local_staging_dir(table_name="t", ds="d")
        try:
            transfers = [
                helpers.FileTransfer("/r/a", f"{ti_dir}/a", "t/d/a"),
                helpers.FileTransfer("/r/b", f"{ti_dir}/b", "t/d/b"),
                helpers.FileTransfer("/r/c", f"{ti_dir}/c", "t/d/c"),
            ]
            summary = helpers.IngestionSummary(table_name="t", ds="d")
            helpers.transfer_files(
                transfers,
                sftp_hook=FakeSFTPHook(failing={"/r/b"}),
                gcs_hook=FakeGCSHook(failing={"t/d/c"}),
                bucket_name="bucket",
                summary=summary,
            )
            assert [r.remote_path for r in summary.uploaded] == ["/r/a"]
            failed = {r.remote_path: r.reason for r in summary.failed}
            assert "/r/b" in failed and "/r/c" in failed
            assert "PermissionError" in failed["/r/b"]
            assert "upload rejected" in failed["/r/c"]
            # The file that failed to upload is still removed locally.
            assert not os.path.exists(f"{ti_dir}/c")
        finally:
            helpers.cleanup_local_path(ti_dir)


class TestLoadSftpToGcs(unittest.TestCase):
    def setUp(self):
        self.staging_dirs = []
        self._original_create = helpers.create_local_staging_dir

    def _patch_hooks(self, sftp_hook, gcs_hook):
        original_sftp = sftp_source.SFTPHook
        original_gcs = sftp_source.GCSHook
        sftp_source.SFTPHook = lambda *args, **kwargs: sftp_hook
        sftp_source.GCSHook = lambda *args, **kwargs: gcs_hook

        def restore():
            sftp_source.SFTPHook = original_sftp
            sftp_source.GCSHook = original_gcs

        self.addCleanup(restore)

    def _staging_dir(self, **kwargs):
        path = self._original_create(**kwargs)
        self.staging_dirs.append(path)
        return path

    def tearDown(self):
        for path in self.staging_dirs:
            helpers.cleanup_local_path(path)

    def test_pushes_summary_and_skips_already_uploaded(self):
        builder = make_builder()
        ti = FakeTaskInstance()
        ti.xcom_push("file_list", ["a.csv", "b.csv", "c.csv"])
        self._patch_hooks(
            FakeSFTPHook(failing={"/remote/b.csv"}),
            FakeGCSHook(existing={"prefix/table/2024-01-01/c.csv"}),
        )

        with self.assertRaises(AirflowException):
            builder.load_sftp_to_gcs(
                table_config=FakeTableConfig(),
                dir_prefix="/remote",
                gcs_bucket_prefix="prefix",
                bucket="bucket",
                ds="2024-01-01",
                ti=ti,
                prev_ds="2023-12-31",
            )

        summary = ti.pushed[helpers.INGESTION_SUMMARY_XCOM_KEY]
        assert summary["uploaded_count"] == 1
        assert summary["skipped_count"] == 1
        assert summary["failed_count"] == 1
        assert summary["uploaded_files"][0]["file"] == "/remote/a.csv"
        assert summary["uploaded_files"][0]["gcs_object"] == "prefix/table/2024-01-01/a.csv"
        assert "already present" in summary["skipped_files"][0]["reason"]
        assert summary["failed_files"][0]["file"] == "/remote/b.csv"
        assert "PermissionError" in summary["failed_files"][0]["reason"]

    def test_all_success_does_not_raise_and_cleans_up(self):
        builder = make_builder()
        ti = FakeTaskInstance()
        ti.xcom_push("file_list", ["a.csv"])
        gcs_hook = FakeGCSHook()
        self._patch_hooks(FakeSFTPHook(), gcs_hook)

        original_staging = sftp_source.create_local_staging_dir
        created = []

        def tracking_create(**kwargs):
            path = self._staging_dir(**kwargs)
            created.append(path)
            return path

        sftp_source.create_local_staging_dir = tracking_create
        self.addCleanup(setattr, sftp_source, "create_local_staging_dir", original_staging)

        builder.load_sftp_to_gcs(
            table_config=FakeTableConfig(),
            dir_prefix="/remote",
            gcs_bucket_prefix="",
            bucket="bucket",
            ds="2024-01-01",
            ti=ti,
            prev_ds="2023-12-31",
        )

        assert gcs_hook.uploaded == ["table/2024-01-01/a.csv"]
        assert created and not os.path.exists(created[0])
        summary = ti.pushed[helpers.INGESTION_SUMMARY_XCOM_KEY]
        assert summary["status"] == helpers.STATUS_SUCCESS

    def test_gcs_bucket_path_format_mode_unchanged(self):
        builder = make_builder(
            file_source_config={
                "file_prefix_filtering": False,
                "airflow_date_template": "ds",
                "gcs_bucket_path_format_mode": "date",
            }
        )
        ti = FakeTaskInstance()
        ti.xcom_push("file_list", ["a.csv"])
        gcs_hook = FakeGCSHook()
        self._patch_hooks(FakeSFTPHook(), gcs_hook)

        builder.load_sftp_to_gcs(
            table_config=FakeTableConfig(),
            dir_prefix="/remote",
            gcs_bucket_prefix="prefix",
            bucket="bucket",
            ds="2024-01-01",
            ti=ti,
            prev_ds="2023-12-31",
        )
        assert gcs_hook.uploaded == ["prefix/table/2024/01/01/a.csv"]

    def test_local_paths_removed_when_every_file_fails(self):
        builder = make_builder()
        ti = FakeTaskInstance()
        ti.xcom_push("file_list", ["a.csv"])
        self._patch_hooks(FakeSFTPHook(failing={"/remote/a.csv"}), FakeGCSHook())

        created = []
        original = sftp_source.create_local_staging_dir

        def tracking_create(**kwargs):
            path = self._staging_dir(**kwargs)
            created.append(path)
            return path

        sftp_source.create_local_staging_dir = tracking_create
        self.addCleanup(setattr, sftp_source, "create_local_staging_dir", original)

        with self.assertRaises(AirflowException):
            builder.load_sftp_to_gcs(
                table_config=FakeTableConfig(),
                dir_prefix="/remote",
                gcs_bucket_prefix="",
                bucket="bucket",
                ds="2024-01-01",
                ti=ti,
                prev_ds="2023-12-31",
            )
        assert created and not os.path.exists(created[0])

    def test_file_prefix_filtering_still_resolves_matching_files(self):
        builder = make_builder(
            file_source_config={
                "file_prefix_filtering": True,
                "airflow_date_template": "ds",
            }
        )
        ti = FakeTaskInstance()
        ti.xcom_push("file_list", ["part-"])
        gcs_hook = FakeGCSHook()
        sftp_hook = FakeSFTPHook(tree=["part-0001.csv"])
        self._patch_hooks(sftp_hook, gcs_hook)

        builder.load_sftp_to_gcs(
            table_config=FakeTableConfig(),
            dir_prefix="/remote",
            gcs_bucket_prefix="",
            bucket="bucket",
            ds="2024-01-01",
            ti=ti,
            prev_ds="2023-12-31",
        )
        assert gcs_hook.uploaded == ["table/2024-01-01/part-0001.csv"]

    def test_empty_file_list_pushes_empty_summary(self):
        builder = make_builder()
        ti = FakeTaskInstance()
        ti.xcom_push("file_list", [])
        gcs_hook = FakeGCSHook()
        self._patch_hooks(FakeSFTPHook(), gcs_hook)

        builder.load_sftp_to_gcs(
            table_config=FakeTableConfig(),
            dir_prefix="/remote",
            gcs_bucket_prefix="",
            bucket="bucket",
            ds="2024-01-01",
            ti=ti,
            prev_ds="2023-12-31",
        )
        assert gcs_hook.uploaded == []
        summary = ti.pushed[helpers.INGESTION_SUMMARY_XCOM_KEY]
        assert summary["total_files"] == 0
        assert summary["status"] == helpers.STATUS_SUCCESS

    def test_run_level_error_still_pushes_summary_and_fails(self):
        builder = make_builder(
            file_source_config={
                "file_prefix_filtering": True,
                "airflow_date_template": "ds",
            }
        )
        ti = FakeTaskInstance()
        ti.xcom_push("file_list", ["a.csv"])

        class BrokenSFTP:
            def list_directory(self, path):
                raise ConnectionError("cannot list /remote")

        self._patch_hooks(BrokenSFTP(), FakeGCSHook())

        with self.assertRaises(AirflowException) as context:
            builder.load_sftp_to_gcs(
                table_config=FakeTableConfig(),
                dir_prefix="/remote",
                gcs_bucket_prefix="",
                bucket="bucket",
                ds="2024-01-01",
                ti=ti,
                prev_ds="2023-12-31",
            )
        assert "cannot list /remote" in str(context.exception)

        summary = ti.pushed[helpers.INGESTION_SUMMARY_XCOM_KEY]
        assert summary["status"] == helpers.STATUS_FAILED
        assert "ConnectionError: cannot list /remote" == summary["run_error"]


class TestLoadSftpToGcsFlag(unittest.TestCase):
    def _table_config(self):
        return FakeTableConfig(
            extra_options={
                "file_table_config": {
                    "directory_prefix": "/remote/tree",
                    "parquet_upload_option": "BASH",
                },
                "sftp_table_config": {
                    "date_column": "dt",
                    "full_dir_download": True,
                },
            }
        )

    def _patch_hooks(self, sftp_hook, gcs_hook):
        original_sftp = sftp_source.SFTPHook
        original_gcs = sftp_source.GCSHook
        sftp_source.SFTPHook = lambda *args, **kwargs: sftp_hook
        sftp_source.GCSHook = lambda *args, **kwargs: gcs_hook

        def restore():
            sftp_source.SFTPHook = original_sftp
            sftp_source.GCSHook = original_gcs

        self.addCleanup(restore)

    def test_flag_file_excluded_and_failures_do_not_stop_run(self):
        builder = make_builder()
        ti = FakeTaskInstance()
        tree = ["/remote/tree/_SUCCESS", "/remote/tree/dt=2024-01-01/a.parquet",
                "/remote/tree/dt=2024-01-01/b.parquet"]
        gcs_hook = FakeGCSHook(failing={"table/2024-01-01/dt=2024-01-01/b.parquet"})
        self._patch_hooks(FakeSFTPHook(tree=tree), gcs_hook)

        with self.assertRaises(AirflowException):
            builder.load_sftp_to_gcs_flag(
                table_config=self._table_config(),
                dir_prefix="/remote/tree",
                gcs_bucket_prefix="",
                bucket="bucket",
                flag_file_path="/remote/tree/_SUCCESS",
                ds="2024-01-01",
                ti=ti,
                prev_ds="2023-12-31",
            )

        # The flag file is never uploaded.
        assert all("_SUCCESS" not in obj for obj in gcs_hook.uploaded)
        summary = ti.pushed[helpers.INGESTION_SUMMARY_XCOM_KEY]
        assert summary["uploaded_count"] == 1
        assert summary["failed_count"] == 1
        assert summary["uploaded_files"][0]["gcs_object"] == "table/2024-01-01/dt=2024-01-01/a.parquet"
        assert summary["failed_files"][0]["file"] == "/remote/tree/dt=2024-01-01/b.parquet"
        # The skipped entry for the flag file is recorded but is not a failure.
        assert any("_SUCCESS" in r["file"] for r in summary["skipped_files"])

    def test_partition_prefix_pushed_on_success(self):
        builder = make_builder()
        ti = FakeTaskInstance()
        tree = ["/remote/tree/par1=a/par2=b/file.parquet"]
        gcs_hook = FakeGCSHook()
        self._patch_hooks(FakeSFTPHook(tree=tree), gcs_hook)

        builder.load_sftp_to_gcs_flag(
            table_config=self._table_config(),
            dir_prefix="/remote/tree",
            gcs_bucket_prefix="",
            bucket="bucket",
            flag_file_path="/remote/tree/_SUCCESS",
            ds="2024-01-01",
            ti=ti,
            prev_ds="2023-12-31",
        )
        assert ti.pushed["partition_prefix"] == "par1=a"
        assert gcs_hook.uploaded == ["table/2024-01-01/par1=a/par2=b/file.parquet"]

    def test_rerun_skips_uploaded_files(self):
        builder = make_builder()
        ti = FakeTaskInstance()
        tree = ["/remote/tree/dt=2024-01-01/a.parquet"]
        gcs_hook = FakeGCSHook(existing={"table/2024-01-01/dt=2024-01-01/a.parquet"})
        self._patch_hooks(FakeSFTPHook(tree=tree), gcs_hook)

        builder.load_sftp_to_gcs_flag(
            table_config=self._table_config(),
            dir_prefix="/remote/tree",
            gcs_bucket_prefix="",
            bucket="bucket",
            flag_file_path="/remote/tree/_SUCCESS",
            ds="2024-01-01",
            ti=ti,
            prev_ds="2023-12-31",
        )
        assert gcs_hook.uploaded == []
        summary = ti.pushed[helpers.INGESTION_SUMMARY_XCOM_KEY]
        assert summary["skipped_count"] == 1
        assert summary["status"] == helpers.STATUS_SUCCESS

    def test_partition_prefix_from_configured_source_option(self):
        builder = make_builder(
            sftp_source_config={
                "sftp_connection_name": "sftp_default",
                "partition_prefix": "date_partition",
            }
        )
        ti = FakeTaskInstance()
        tree = ["/remote/tree/file.parquet"]
        self._patch_hooks(FakeSFTPHook(tree=tree), FakeGCSHook())

        builder.load_sftp_to_gcs_flag(
            table_config=self._table_config(),
            dir_prefix="/remote/tree",
            gcs_bucket_prefix="",
            bucket="bucket",
            flag_file_path="/remote/tree/_SUCCESS",
            ds="2024-01-01",
            ti=ti,
            prev_ds="2023-12-31",
        )
        assert ti.pushed["partition_prefix"] == "date_partition"

    def test_full_dir_download_false_uses_dated_subdirectory(self):
        builder = make_builder()
        ti = FakeTaskInstance()
        records = {}

        class RecordingSFTP(FakeSFTPHook):
            def get_tree_map(self, prefix):
                records["prefix"] = prefix
                return []

        self._patch_hooks(RecordingSFTP(), FakeGCSHook())

        builder.load_sftp_to_gcs_flag(
            table_config=FakeTableConfig(
                extra_options={
                    "file_table_config": {
                        "directory_prefix": "/remote/tree",
                        "parquet_upload_option": "BASH",
                    },
                    "sftp_table_config": {
                        "date_column": "dt",
                        "full_dir_download": False,
                    },
                }
            ),
            dir_prefix="/remote/tree",
            gcs_bucket_prefix="",
            bucket="bucket",
            flag_file_path="/remote/tree/_SUCCESS",
            ds="2024-01-01",
            ti=ti,
            prev_ds="2023-12-31",
        )
        assert records["prefix"] == "/remote/tree/dt=2024-01-01"
        # No files: no partition prefix is derived and no error is raised.
        assert "partition_prefix" not in ti.pushed
        assert ti.pushed[helpers.INGESTION_SUMMARY_XCOM_KEY]["status"] == helpers.STATUS_SUCCESS


if __name__ == "__main__":
    unittest.main()
