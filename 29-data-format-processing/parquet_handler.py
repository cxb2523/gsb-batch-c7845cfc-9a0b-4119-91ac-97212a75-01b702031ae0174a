# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Parquet read/write with pyarrow via pandas.
Parquet is the lake standard: columnar, typed, compressed, and splittable.
Land raw as CSV/JSONL if you must, but convert to Parquet before anything
reads the data twice.
"""

import time
from pathlib import Path

import pandas as pd


def write_parquet(df: pd.DataFrame, out_path: str | Path, compression: str = "snappy") -> int:
    """Write a DataFrame to Parquet.

    Why Parquet over CSV for analytical workloads:
      - Columnar: a query touching 2 of 9 columns reads ~2/9 of the bytes.
        BigQuery, Spark, and DuckDB all exploit this (column pruning).
      - Predicate pushdown: row-group min/max stats let engines skip whole
        chunks without reading them.
      - Schema travels with the data: dtypes and dates survive the roundtrip
        without re-inference (CSV re-parses strings on every read).
      - snappy = default, fast, decent ratio. Use zstd for colder data where
        storage cost beats CPU cost; gzip only for legacy compatibility.

    Args:
        df: DataFrame to write.
        out_path: Target .parquet file path.
        compression: "snappy" (default), "zstd", or "gzip".

    Returns:
        File size in bytes.
    """
    out_path = Path(out_path)
    df.to_parquet(out_path, engine="pyarrow", compression=compression, index=False)
    return out_path.stat().st_size


def read_parquet(path: str | Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Read a Parquet file, optionally with column pruning.

    Passing columns= is where the columnar format pays off — only the
    requested columns are deserialized, so reading 2 of 9 columns is
    dramatically faster than reading the full file. CSV cannot do this.

    Args:
        path: Path to the .parquet file.
        columns: Optional list of column names to read. None = all columns.

    Returns:
        DataFrame with embedded schema (types preserved, no re-inference needed).
    """
    return pd.read_parquet(path, columns=columns)


def benchmark(csv_path: str | Path, parquet_path: str | Path) -> dict:
    """Compare read speed and file size between CSV and Parquet.

    Args:
        csv_path: Source CSV file path.
        parquet_path: Equivalent Parquet file path (must already exist).

    Returns:
        Dict with keys: csv_size, parquet_size, csv_read_ms, parquet_read_ms,
        parquet_slim_ms (2-column pruned read).
    """
    csv_path = Path(csv_path)
    parquet_path = Path(parquet_path)

    csv_size = csv_path.stat().st_size
    pq_size = parquet_path.stat().st_size

    t0 = time.perf_counter()
    pd.read_csv(csv_path, parse_dates=["order_date"])
    csv_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    pd.read_parquet(parquet_path)
    pq_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    pd.read_parquet(parquet_path, columns=["region", "total_amount"])
    slim_ms = (time.perf_counter() - t0) * 1000

    return {
        "csv_size": csv_size,
        "parquet_size": pq_size,
        "csv_read_ms": csv_ms,
        "parquet_read_ms": pq_ms,
        "parquet_slim_ms": slim_ms,
    }
