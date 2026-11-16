import csv
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make the DAG folder importable no matter where pytest is invoked from, the
# same way Airflow puts its dags folder on the path when parsing.
DAGS_DIR = Path(__file__).resolve().parents[2] / "dags"
if str(DAGS_DIR) not in sys.path:
    sys.path.insert(0, str(DAGS_DIR))

import etl  # noqa: E402  (import after sys.path tweak)
from cryptocurrency_data_pipeline import dag as crypto_dag  # noqa: E402

DS = "2022-02-01"
OBJECT_KEY = f"cryptocurrency/{DS}/shib.csv"


def _ohlcv_rows(count=24):
    """Deterministic OHLCV payload shaped like a ccxt response."""
    start = int(datetime(2022, 2, 1).timestamp() * 1000)
    return [
        [
            start + i * 60 * 60 * 1000,  # timestamp (1h candles)
            0.00002000 + i,  # open
            0.00003000 + i,  # highest
            0.00001000 + i,  # lowest
            0.00002500 + i,  # closing
            1000.0 + i,  # volume
        ]
        for i in range(count)
    ]


def _fake_exchange(rows):
    exchange = MagicMock()
    exchange.fetch_ohlcv.return_value = rows
    return exchange


def _run_task(task_id, context):
    """Execute a task through its operator, not by calling etl directly."""
    return crypto_dag.get_task(task_id).execute(context)


def _read_csv(path):
    with open(path, newline="") as f:
        return list(csv.reader(f))


# ---------------------------------------------------------------------------
# DAG wiring
# ---------------------------------------------------------------------------


def test_dag_uses_real_operators_and_keeps_task_ids():
    from airflow.operators.dummy import DummyOperator
    from airflow.operators.email import EmailOperator
    from airflow.operators.python import PythonOperator
    from airflow.providers.postgres.operators.postgres import PostgresOperator

    tasks = {task.task_id: task for task in crypto_dag.tasks}

    assert set(tasks) == {
        "fetch_ohlcv",
        "download_file",
        "create_import_table",
        "load_data_into_database",
        "create_final_table",
        "merge_import_into_final_table",
        "clear_import_table",
        "notify",
    }

    # Every placeholder must be gone.
    assert not any(isinstance(task, DummyOperator) for task in tasks.values())

    assert isinstance(tasks["fetch_ohlcv"], PythonOperator)
    assert isinstance(tasks["download_file"], PythonOperator)
    assert isinstance(tasks["load_data_into_database"], PythonOperator)
    assert isinstance(tasks["create_import_table"], PostgresOperator)
    assert isinstance(tasks["create_final_table"], PostgresOperator)
    assert isinstance(tasks["merge_import_into_final_table"], PostgresOperator)
    assert isinstance(tasks["clear_import_table"], PostgresOperator)
    assert isinstance(tasks["notify"], EmailOperator)


def test_dag_dependencies_are_chained_in_order():
    chain = [
        "fetch_ohlcv",
        "download_file",
        "create_import_table",
        "load_data_into_database",
        "create_final_table",
        "merge_import_into_final_table",
        "clear_import_table",
        "notify",
    ]
    for upstream_id, downstream_id in zip(chain, chain[1:]):
        upstream = crypto_dag.get_task(upstream_id)
        downstream = crypto_dag.get_task(downstream_id)
        assert downstream_id in {t.task_id for t in upstream.downstream_list}
        assert upstream_id in {t.task_id for t in downstream.upstream_list}


def test_default_args_preserved():
    start_date = crypto_dag.default_args["start_date"]
    assert crypto_dag.default_args["owner"] == "zkan"
    # Airflow/pendulum attaches UTC, so compare the calendar date fields.
    assert (start_date.year, start_date.month, start_date.day) == (2022, 2, 1)
    assert crypto_dag.get_task("fetch_ohlcv").owner == "zkan"


def test_fetch_and_load_have_bounded_retries():
    for task_id in ("fetch_ohlcv", "load_data_into_database"):
        retries = crypto_dag.get_task(task_id).retries
        assert isinstance(retries, int)
        assert retries > 0


# ---------------------------------------------------------------------------
# fetch_ohlcv
# ---------------------------------------------------------------------------


def test_fetch_ohlcv_writes_24_rows_and_uploads(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rows = _ohlcv_rows(24)
    exchange = _fake_exchange(rows)
    hook = MagicMock()

    with patch.object(etl.ccxt, "binance", return_value=exchange) as binance, patch.object(
        etl, "S3Hook", return_value=hook
    ):
        _run_task("fetch_ohlcv", {"ds": DS})

    # Binance was asked for 24 one-hour SHIB/USDT candles starting at ds.
    binance.assert_called_once_with()
    args, kwargs = exchange.fetch_ohlcv.call_args
    assert args == ("SHIB/USDT",)
    assert kwargs["timeframe"] == "1h"
    assert kwargs["limit"] == 24
    assert (
        datetime.fromtimestamp(kwargs["since"] / 1000).strftime("%Y-%m-%d") == DS
    )

    # CSV holds exactly the 24 fetched rows.
    csv_path = tmp_path / f"shib-{DS}.csv"
    assert csv_path.exists()
    assert len(_read_csv(csv_path)) == 24

    # And it was uploaded to the agreed object key.
    hook.load_file.assert_called_once()
    load_args, load_kwargs = hook.load_file.call_args
    assert load_args[0] == f"shib-{DS}.csv"
    assert load_kwargs["key"] == OBJECT_KEY
    assert load_kwargs["bucket_name"] == "datalake"
    assert load_kwargs["replace"] is True


def test_fetch_ohlcv_fails_and_does_not_upload_when_exchange_returns_empty(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    exchange = _fake_exchange([])
    hook = MagicMock()

    with patch.object(etl.ccxt, "binance", return_value=exchange), patch.object(
        etl, "S3Hook", return_value=hook
    ):
        with pytest.raises(Exception) as excinfo:
            _run_task("fetch_ohlcv", {"ds": DS})

    # Airflow needs an exception it recognises to mark the task failed.
    assert isinstance(excinfo.value, etl.EmptyMarketDataError)
    hook.load_file.assert_not_called()
    # No empty CSV should be written either.
    assert not (tmp_path / f"shib-{DS}.csv").exists()
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# download_file -> load_data_into_database (XCom hand-off)
# ---------------------------------------------------------------------------


def test_download_path_is_the_file_loaded_into_database(tmp_path):
    local_csv = tmp_path / "downloaded-shib.csv"
    with open(local_csv, "w", newline="") as f:
        csv.writer(f).writerows(_ohlcv_rows(24))

    # A tiny stand-in for Airflow's XCom store: what download_file returns is
    # pushed as the "return_value" XCom, and load_data_into_database can only
    # get the path back by pulling that key.
    xcom_store = {}

    class FakeTaskInstance:
        def xcom_push(self, key, value):
            xcom_store[key] = value

        def xcom_pull(self, task_ids=None, key="return_value"):
            assert task_ids == "download_file"
            return xcom_store.get(key)

    ti = FakeTaskInstance()

    # 1) download_file returns the local path, which the operator pushes to XCom.
    download_hook = MagicMock()
    download_hook.download_file.return_value = str(local_csv)
    with patch.object(etl, "S3Hook", return_value=download_hook):
        downloaded_path = _run_task("download_file", {"ds": DS, "ti": ti})
    ti.xcom_push("return_value", downloaded_path)

    assert downloaded_path == str(local_csv)
    download_hook.download_file.assert_called_once_with(
        key=OBJECT_KEY,
        bucket_name="datalake",
    )

    # 2) load_data_into_database pulls that path from XCom and COPYs from it.
    copied = {}

    def fake_copy_expert(sql, filename, *args, **kwargs):
        assert "cryptocurrency_import" in sql
        with open(filename, newline="") as f:
            copied["rows"] = list(csv.reader(f))

    postgres_hook = MagicMock()
    postgres_hook.copy_expert.side_effect = fake_copy_expert

    with patch.object(etl, "PostgresHook", return_value=postgres_hook):
        _run_task("load_data_into_database", {"ds": DS, "ti": ti})

    postgres_hook.copy_expert.assert_called_once()
    assert postgres_hook.copy_expert.call_args[0][1] == downloaded_path
    assert len(copied["rows"]) == 24
    assert copied["rows"][0][0] == str(
        int(datetime(2022, 2, 1).timestamp() * 1000)
    )
