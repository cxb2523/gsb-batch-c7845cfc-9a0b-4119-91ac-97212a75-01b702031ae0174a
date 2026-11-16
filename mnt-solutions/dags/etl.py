import csv
import logging
from datetime import datetime

import ccxt
from airflow.exceptions import AirflowException
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.postgres.hooks.postgres import PostgresHook

S3_CONN_ID = "minio"
S3_BUCKET = "datalake"
POSTGRES_CONN_ID = "postgres"
SYMBOL = "SHIB/USDT"
TIMEFRAME = "1h"
OHLCV_LIMIT = 24


class EmptyMarketDataError(AirflowException, ValueError):
    """The exchange had no candles for the scheduled day.

    Inherits from ``AirflowException`` so the task is marked failed by Airflow,
    and from ``ValueError`` because the response is a bad value for the
    requested date.
    """


def _ohlcv_object_key(ds):
    """Object storage key used to hand the daily CSV over to the download step."""
    return f"cryptocurrency/{ds}/shib.csv"


def _fetch_ohlcv(**context):
    ds = context["ds"]

    # Choose Binance exchange
    exchange = ccxt.binance()

    # Fetch data from exchange
    dt_obj = datetime.strptime(ds, "%Y-%m-%d")
    millisec = int(dt_obj.timestamp() * 1000)
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, since=millisec, limit=OHLCV_LIMIT)
    logging.info(f"OHLCV of {SYMBOL} value. [ds={ds}, ohlcv={ohlcv}]")

    # An empty response means there is no market data for that day. Fail the
    # task *before* writing or uploading anything so downstream steps never
    # see an empty data lake object.
    if not ohlcv:
        raise EmptyMarketDataError(
            f"No OHLCV data returned for {SYMBOL} on {ds}; treating it as a "
            "day without market data."
        )

    # Write OHLCV data to CSV file
    file_name = f"shib-{ds}.csv"
    with open(file_name, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(ohlcv)
    logging.info(f"Wrote {len(ohlcv)} rows of OHLCV data to {file_name}.")

    # Store CSV file in S3
    s3_hook = S3Hook(aws_conn_id=S3_CONN_ID)
    s3_hook.load_file(
        file_name,
        key=_ohlcv_object_key(ds),
        bucket_name=S3_BUCKET,
        replace=True,
    )
    logging.info(
        f"Uploaded {file_name} to s3://{S3_BUCKET}/{_ohlcv_object_key(ds)}."
    )


def _download_file(**context):
    ds = context["ds"]

    # Download file from S3
    s3_hook = S3Hook(aws_conn_id=S3_CONN_ID)
    file_name = s3_hook.download_file(
        key=_ohlcv_object_key(ds),
        bucket_name=S3_BUCKET,
    )
    logging.info(
        f"Downloaded s3://{S3_BUCKET}/{_ohlcv_object_key(ds)} to {file_name}."
    )

    return file_name


def _load_data_into_database(**context):
    postgres_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)

    # Get file name from XComs
    file_name = context["ti"].xcom_pull(task_ids="download_file", key="return_value")
    logging.info(f"Loading OHLCV data into cryptocurrency_import from {file_name}.")

    # Copy file to database
    postgres_hook.copy_expert(
        """
            COPY
                cryptocurrency_import
            FROM STDIN DELIMITER ',' CSV
        """,
        file_name,
    )
