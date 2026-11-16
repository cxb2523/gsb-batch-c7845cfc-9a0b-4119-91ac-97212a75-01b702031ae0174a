from dataclasses import fields
from os import X_OK
from urllib.parse import urlparse
import logging
from dacite import from_dict
from dataclasses import dataclass
from datetime import datetime
import os
import glob

from airflow.operators.python import PythonOperator
from airflow.operators.dummy import DummyOperator
from airflow.providers.google.cloud.hooks.gcs import GCSHook
from airflow.sensors.python import PythonSensor
from airflow.models import Variable

from airflow.providers.sftp.hooks.sftp import SFTPHook
from gcp_airflow_foundations.operators.api.sensors.sftp_sensor import SFTPFilesExistenceSensor
from gcp_airflow_foundations.operators.api.sensors.sftp_prefix_sensor import SFTPFilesExistencePrefixSensor
from gcp_airflow_foundations.source_class.generic_file_source import GenericFileIngestionDagBuilder
from gcp_airflow_foundations.common.gcp.load_builder import load_builder
from gcp_airflow_foundations.base_class.sftp_table_config import SFTPTableConfig
from gcp_airflow_foundations.common.sftp.helpers import (
    INGESTION_SUMMARY_XCOM_KEY,
    FileTransfer,
    IngestionSummary,
    cleanup_local_path,
    create_local_staging_dir,
    finalize_ingestion,
    remove_private_key_file,
    save_id_to_file,
    transfer_files,
)


class SFTPFileIngestionDagBuilder(GenericFileIngestionDagBuilder):
    """
    Builds DAGs to load files from an SFTP server to a BigQuery Table. Authenticates with the Airflow SFTP provider connection.

    For specifying files to be uploaded, there are 3 options:
        1) A metadata file is provided with templated daily file names (templating supports {{ ds }} and {{ TABLE_NAME }})
        2) The field templated_file_name is provided in the table config (the same templating applies - but only 1 file can be provided in this manner)
        3) A directory can be specified for .PARQUET files only in the table config

    If a metadata file is provided, it can be a fixed file, or can be a new file supplied daily.
    Airflow context variables are supported for the file naming, e.g.
        TABLE_METADATA_FILE_{{ ds }}.csv
    for a metadata file supplied daily.

    The format of the metadata file should be a csv with one column as follows:
        FILE_NAME_1
        ...
        FILE_NAME_N
    with all files to ingest

    If a directory for .PARQUET ingestion is provided, the field directory_prefix should point to the directory where
    the outward-most external partition sits, and the field flag_file_path should point to the path of the _SUCCESS file on the sftp server.
    A mod_time check occurs on the flag file to screen for daily ingestion on the sensor.
    """
    source_type = "SFTP"

    def flag_file_sensor(self, table_config, taskgroup):
        if "flag_file_path" in table_config.extra_options.get("file_table_config"):
            dir_prefix = table_config.extra_options.get("file_table_config")["directory_prefix"]
            flag_file_path = table_config.extra_options.get("file_table_config")["flag_file_path"]
            return self.get_sftp_sensor(table_config, taskgroup, dir_prefix, flag_file_path)
        else:
            return None

    def file_ingestion_task(self, table_config, taskgroup):
        """
        Ingest from SFTP
        """
        dir_prefix = table_config.extra_options.get("file_table_config")["directory_prefix"]
        if "flag_file_path" in table_config.extra_options.get("file_table_config"):
            flag_file_path = table_config.extra_options.get("file_table_config")["flag_file_path"]
        else:
            flag_file_path = ""
        gcs_bucket_prefix = self.config.source.extra_options["file_source_config"]["gcs_bucket_prefix"]
        bucket = self.config.source.extra_options["gcs_bucket"]
        return self.get_sftp_ingestion_operator(table_config, taskgroup, dir_prefix, gcs_bucket_prefix, flag_file_path, bucket)

    def get_file_list_task(self, table_config, taskgroup):
        return PythonOperator(
            task_id="get_file_list",
            op_kwargs={"table_config": table_config},
            python_callable=self.get_list_of_files,
            task_group=taskgroup)

    def get_list_of_files(self, table_config, **kwargs):
        # gcs_hook = GCSHook()
        airflow_date_template = self.config.source.extra_options["file_source_config"]["airflow_date_template"]
        if airflow_date_template == "ds":
            ds = kwargs["ds"]
        else:
            ds = kwargs["prev_ds"]
        ds = datetime.strptime(str(ds), "%Y-%m-%d").strftime(self.config.source.extra_options["file_source_config"]["date_format"])
        # XCom push the list of files
        # overwrite if in table_config
        dir_prefix = table_config.extra_options.get("file_table_config")["directory_prefix"]
        dir_prefix = dir_prefix.replace("{{ ds }}", ds)

        # gcs_bucket_prefix = self.config.source.extra_options["file_source_config"]["gcs_bucket_prefix"]

        if self.config.source.extra_options["file_source_config"]["source_format"] == "PARQUET":
            file_list = [dir_prefix]
            kwargs['ti'].xcom_push(key='file_list', value=file_list)
            return
        else:
            # bucket = self.config.source.extra_options["gcs_bucket"]
            if "metadata_file" in table_config.extra_options.get("file_table_config"):
                # metadata_file_name = table_config.extra_options.get("file_table_config")["metadata_file"]
                # metadata_file = gcs_hook.download(bucket_name=bucket, object_name=metadata_file_name, filename="metadata.csv")
                file_list = []
                with open('metadata.csv', newline='') as f:
                    for line in f:
                        file_list.append(line.strip())
            else:
                templated_file_name = self.config.source.extra_options["file_source_config"]["file_name_template"]
                templated_file_name = templated_file_name.replace("{{ TABLE_NAME }}", table_config.table_name)
                file_list = [templated_file_name]

            # support replacing files with current dates
            file_list[:] = [file.replace("{{ ds }}", ds) if "{{ ds }}" in file else file for file in file_list]

        kwargs['ti'].xcom_push(key='file_list', value=file_list)

    def file_sensor(self, table_config, taskgroup):
        """
        Returns an Airflow sensor that waits for the list of files specified by the metadata file provided.
        """
        # If we have a flag file for ingestion, there is no need to check the files specifically
        if "flag_file_path" in table_config.extra_options.get("file_table_config"):
            return None

        # bucket = self.config.source.extra_options["gcs_bucket"]
        table_name = table_config.table_name
        files_to_wait_for = "{{" + f"ti.xcom_pull(key='file_list', task_ids='{table_name}.ftp_taskgroup.get_file_list')" + "}}"
        timeout = self.config.source.extra_options["file_source_config"]["sensor_timeout"]
        sftp_conn = self.config.source.extra_options["sftp_source_config"]["sftp_connection_name"]
        file_prefix_filtering = timeout = self.config.source.extra_options["file_source_config"]["file_prefix_filtering"]
        dir_prefix = table_config.extra_options.get("file_table_config")["directory_prefix"]

        if file_prefix_filtering:
            if "sftp_private_key_secret_name" in self.config.source.extra_options["sftp_source_config"]:
                key_name = self.config.source.extra_options["sftp_source_config"]["sftp_private_key_secret_name"]
                return SFTPFilesExistencePrefixSensor(
                    task_id="wait_for_files_to_ingest",
                    path=dir_prefix,
                    prefixes=files_to_wait_for,
                    key_auth=True,
                    key_name=key_name,
                    task_group=taskgroup,
                    sftp_conn_id=sftp_conn,
                    timeout=timeout
                )
            else:
                return SFTPFilesExistencePrefixSensor(
                    task_id="wait_for_files_to_ingest",
                    path=dir_prefix,
                    prefixes=files_to_wait_for,
                    task_group=taskgroup,
                    sftp_conn_id=sftp_conn,
                    timeout=timeout
                )
        else:
            if "sftp_private_key_secret_name" in self.config.source.extra_options["sftp_source_config"]:
                key_name = self.config.source.extra_options["sftp_source_config"]["sftp_private_key_secret_name"]
                return SFTPFilesExistenceSensor(
                    task_id="wait_for_files_to_ingest",
                    dir_prefix=dir_prefix,
                    paths=files_to_wait_for,
                    key_auth=True,
                    key_name=key_name,
                    task_group=taskgroup,
                    sftp_conn_id=sftp_conn,
                    timeout=timeout
                )
            else:
                return SFTPFilesExistenceSensor(
                    task_id="wait_for_files_to_ingest",
                    dir_prefix=dir_prefix,
                    paths=files_to_wait_for,
                    task_group=taskgroup,
                    sftp_conn_id=sftp_conn,
                    timeout=timeout
                )

    def get_sftp_sensor(self, table_config, taskgroup, dir_prefix, flag_file_path):
        return PythonSensor(
            task_id="sftp_sensor",
            op_kwargs={"table_config": table_config,
                       "dir_prefix": dir_prefix,
                       "flag_file_path": flag_file_path},
            python_callable=self.sftp_flag_sensor,
            task_group=taskgroup
        )

    def sftp_flag_sensor(self, table_config, dir_prefix, flag_file_path, **kwargs):
        # Check if the flag file is dated with the execution date or later - if yes, then the files are ready for ingestion.
        # allows backfilling
        sftp_conn = self.config.source.extra_options["sftp_source_config"]["sftp_connection_name"]

        ds = kwargs["ds"]
        if "sftp_private_key_secret_name" in self.config.source.extra_options["sftp_source_config"]:
            private_key_name = self.config.source.extra_options["sftp_source_config"]["sftp_private_key_secret_name"]
            save_id_to_file(private_key_name)
        sftp_hook = SFTPHook(ssh_conn_id=sftp_conn)
        dir_prefix = dir_prefix.replace("{{ ds }}", ds)
        mod_time = sftp_hook.get_mod_time(flag_file_path)
        mod_time = datetime.strptime(mod_time, '%Y%m%d%H%M%S').strftime('%Y-%m-%d')
        if "sftp_private_key_secret_name" in self.config.source.extra_options["sftp_source_config"]:
            if os.path.isfile("id_rsa"):
                os.remove("id_rsa")

        return ds <= mod_time

    def get_sftp_ingestion_operator(self, table_config, taskgroup, dir_prefix, gcs_bucket_prefix, flag_file_path, bucket):
        if not flag_file_path == "":
            return PythonOperator(
                task_id="load_sftp_to_gcs",
                op_kwargs={
                    "table_config": table_config,
                    "dir_prefix": dir_prefix,
                    "gcs_bucket_prefix": gcs_bucket_prefix,
                    "bucket": bucket,
                    "flag_file_path": flag_file_path},
                python_callable=self.load_sftp_to_gcs_flag,
                task_group=taskgroup
            )
        else:
            return PythonOperator(
                task_id="load_sftp_to_gcs",
                op_kwargs={
                    "table_config": table_config,
                    "dir_prefix": dir_prefix,
                    "gcs_bucket_prefix": gcs_bucket_prefix,
                    "bucket": bucket},
                python_callable=self.load_sftp_to_gcs,
                task_group=taskgroup
            )

    # flake8: noqa: C901
    def load_sftp_to_gcs(self, table_config, dir_prefix, gcs_bucket_prefix, bucket, **kwargs):
        """Load the files listed for this ``ds`` from SFTP into GCS.

        Files are settled one by one: a failing file neither aborts the run nor
        hides the files that were already uploaded. The per-file outcome is
        pushed to XCom and the task fails at the end if anything failed. Files
        that already exist in GCS for this ``ds`` are skipped, so re-running the
        same ``ds`` does not upload duplicates.
        """
        ds = kwargs["ds"]
        ti = kwargs["ti"]
        table_name = table_config.table_name
        sftp_conn = self.config.source.extra_options["sftp_source_config"]["sftp_connection_name"]

        if gcs_bucket_prefix is None:
            gcs_bucket_prefix = ""
        if not gcs_bucket_prefix == "":
            gcs_bucket_prefix += "/"

        summary = IngestionSummary(table_name=table_name, ds=ds)
        uses_private_key = "sftp_private_key_secret_name" in self.config.source.extra_options["sftp_source_config"]
        staging_dir = None

        try:
            if uses_private_key:
                private_key_name = self.config.source.extra_options["sftp_source_config"]["sftp_private_key_secret_name"]
                save_id_to_file(private_key_name)
            sftp_hook = SFTPHook(sftp_conn)

            files_to_load = ti.xcom_pull(key='file_list', task_ids=f'{table_name}.ftp_taskgroup.get_file_list') or []

            if self.config.source.extra_options["file_source_config"]["file_prefix_filtering"]:
                file_name_prefixes = [f.split("/")[-1] for f in files_to_load]
                full_file_names = []
                dir = sftp_hook.list_directory(dir_prefix)
                for prefix in file_name_prefixes:
                    for f in dir:
                        if f.startswith(prefix):
                            full_file_names.append(f)
                        break
                files_to_load = full_file_names

            destination_path_prefix = gcs_bucket_prefix + table_name + "/" + ds
            if "gcs_bucket_path_format_mode" in self.config.source.extra_options["file_source_config"]:
                date = datetime.strptime(str(ds), '%Y-%m-%d').strftime('%Y/%m/%d')
                destination_path_prefix = gcs_bucket_prefix + table_name + "/" + date

            staging_dir = create_local_staging_dir(table_name=table_name, ds=ds)
            transfers = [
                FileTransfer(
                    remote_path=dir_prefix + "/" + file,
                    local_path=staging_dir + "/" + file,
                    object_name=destination_path_prefix + "/" + file,
                )
                for file in files_to_load
            ]

            transfer_files(
                files=transfers,
                sftp_hook=sftp_hook,
                gcs_hook=GCSHook(),
                bucket_name=bucket,
                summary=summary,
            )
        except Exception as exc:
            # A run-level failure (connection, listing, ...) is recorded in the
            # summary so the run still ends with a readable account of what
            # happened before the exception is surfaced by finalize_ingestion.
            summary.record_run_error(exc)
            logging.exception("SFTP ingestion failed before completing: %s", exc)
        finally:
            # Never skip cleanup because a single file failed, and only ever
            # remove this run's own staging directory.
            cleanup_local_path(staging_dir)
            if uses_private_key:
                remove_private_key_file()

        finalize_ingestion(ti, summary, xcom_key=INGESTION_SUMMARY_XCOM_KEY)

    # flake8: noqa: C901
    def load_sftp_to_gcs_flag(self, table_config, dir_prefix, gcs_bucket_prefix, bucket, flag_file_path, **kwargs):
        """Load a whole SFTP directory tree into GCS, using a flag file as the marker.

        The flag file itself is never uploaded. Every other file is settled
        individually, the run summary is pushed to XCom and the task fails at
        the end if at least one file failed. Files already present in GCS for
        this ``ds`` are skipped.
        """
        ds = kwargs["ds"]
        ti = kwargs["ti"]

        airflow_date_template = self.config.source.extra_options["file_source_config"]["airflow_date_template"]

        if gcs_bucket_prefix is None:
            gcs_bucket_prefix = ""
        if not gcs_bucket_prefix == "":
            gcs_bucket_prefix += "/"

        # flag to check whether we are loading dates named with {{ prev_ds }} or {{ ds }}
        # landing zone table creation will still use {{ ds }}
        source_file_date = ds
        if airflow_date_template == "prev_ds":
            source_file_date = kwargs["prev_ds"]

        date_column = table_config.extra_options.get("sftp_table_config")["date_column"]
        full_dir_download = table_config.extra_options.get("sftp_table_config")["full_dir_download"]
        table_name = table_config.table_name
        destination_path_prefix = gcs_bucket_prefix + table_name + "/" + ds

        uses_private_key = "sftp_private_key_secret_name" in self.config.source.extra_options["sftp_source_config"]
        summary = IngestionSummary(table_name=table_name, ds=ds)
        staging_dir = None
        upload_candidates = []
        partition_prefix = None

        try:
            if uses_private_key:
                private_key_name = self.config.source.extra_options["sftp_source_config"]["sftp_private_key_secret_name"]
                save_id_to_file(private_key_name)
            sftp_hook = SFTPHook()

            # get recursive sftp structure w/ full paths
            tree_map_prefix = dir_prefix
            upload_option = table_config.extra_options.get("file_table_config")["parquet_upload_option"]
            if not full_dir_download and upload_option == "BASH":
                tree_map_prefix += f"/{date_column}={source_file_date}"
            logging.info(tree_map_prefix)
            map = sftp_hook.get_tree_map(tree_map_prefix)
            if map and isinstance(map[0], list):
                map = map[0]

            # get list of files, create local directory structure, download
            files_to_upload = [x for x in (map or [])]
            logging.info(files_to_upload)

            staging_dir = create_local_staging_dir(table_name=table_name, ds=ds)
            # delete prefix up until the external partitions
            local_file_prefix_to_drop = staging_dir + "/" + dir_prefix + "/"
            upload_candidates = [staging_dir + "/" + file for file in files_to_upload]

            transfers = []
            for remote_file in files_to_upload:
                if remote_file == flag_file_path:
                    # Behaviour of the flag-file mode is unchanged: the marker
                    # file is never downloaded nor uploaded. It is still listed
                    # in the summary so the run account is complete.
                    transfers.append(
                        FileTransfer(
                            remote_path=remote_file,
                            local_path=staging_dir + "/" + remote_file,
                            object_name=None,
                            skip_reason="flag file excluded from ingestion",
                        )
                    )
                    continue

                file = staging_dir + "/" + remote_file
                transfers.append(
                    FileTransfer(
                        remote_path=remote_file,
                        local_path=file,
                        object_name=destination_path_prefix + "/" + file.replace(local_file_prefix_to_drop, ""),
                    )
                )

            transfer_files(
                files=transfers,
                sftp_hook=sftp_hook,
                gcs_hook=GCSHook(),
                bucket_name=bucket,
                summary=summary,
            )

            # xcom push the prefix of external partitions, e.g. "par1=val1/par2=val/par3=val3"
            # hacky way to do it - find a cleaner way to fix it later
            if "partition_prefix" not in self.config.source.extra_options["sftp_source_config"]:
                if upload_candidates:
                    # Mirrors the historical derivation: take the parent of the
                    # first file, relative to the configured directory prefix.
                    candidate = upload_candidates[0].rsplit('/', 1)[0]
                    relative_candidate = candidate.replace(staging_dir + "/", "", 1)
                    partition_prefix = relative_candidate.replace(dir_prefix, "", 1).lstrip("/")
                    if "/" in partition_prefix:
                        partition_prefix = partition_prefix.rsplit('/', 1)[0]
            else:
                # TEMPORARY hack to fix ingestion. # TO REMOVE
                if table_config.extra_options.get("file_table_config")["parquet_upload_option"] == "BASH":
                    partition_prefix = self.config.source.extra_options["sftp_source_config"]["partition_prefix"]
                    partition_prefix.replace("date", table_config.extra_options.get("sftp_table_config")["date_column"])
                    partition_prefix.replace("ds", ds)

            if partition_prefix is not None:
                logging.info(partition_prefix)
                ti.xcom_push(key='partition_prefix', value=partition_prefix)
        except Exception as exc:
            summary.record_run_error(exc)
            logging.exception("SFTP ingestion failed before completing: %s", exc)
        finally:
            cleanup_local_path(staging_dir)
            if uses_private_key:
                remove_private_key_file()

        finalize_ingestion(ti, summary, xcom_key=INGESTION_SUMMARY_XCOM_KEY)

    def delete_gcs_files(self, table_config, taskgroup):
        pass

    def validate_extra_options(self):
        super().validate_extra_options()
        tables = self.config.tables
        for table_config in tables:
            # try and parse as SFTPTableConfig
            # sftp_table_config = from_dict(data_class=SFTPTableConfig, data=table_config.extra_options.get("sftp_table_config"))
            pass
