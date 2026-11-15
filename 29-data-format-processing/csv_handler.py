# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
CSV loading — stdlib csv.DictReader (streaming) and pandas.read_csv (typed).
Choose streaming for ETL hops and memory-constrained runtimes;
choose pandas when you need aggregations, joins, or downstream format conversion.
"""

import csv
from pathlib import Path

import pandas as pd


def load_with_stdlib(path: str | Path) -> list[dict]:
    """Load a CSV row-by-row with csv.DictReader.

    Zero dependencies beyond stdlib. Streaming: constant memory regardless
    of file size. All values come back as str — caller owns type conversion.
    Use when: file may not fit in RAM, single-pass ETL, constrained runtime
    (Cloud Functions cold start matters), or forwarding rows to Pub/Sub / an API.

    Args:
        path: Path to the CSV file.

    Returns:
        List of dicts with typed values (order_id, quantity as int;
        unit_price, total_amount as float; rest as str).
    """
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["order_id"] = int(row["order_id"])
            row["quantity"] = int(row["quantity"])
            row["unit_price"] = float(row["unit_price"])
            row["total_amount"] = float(row["total_amount"])
            rows.append(row)
    return rows


def load_with_pandas(path: str | Path) -> pd.DataFrame:
    """Load the full CSV into a typed pandas DataFrame.

    Always pass dtype and parse_dates — inference on large files is slow
    and can silently mis-type columns (e.g. IDs with leading zeros -> int).
    Use when: need aggregations/joins/reshaping; file fits in memory
    (rule of thumb: ~5x file size in RAM for untyped loads); data is
    headed to BigQuery/Parquet anyway.

    Args:
        path: Path to the CSV file.

    Returns:
        DataFrame with correct dtypes (order_date as datetime64).
    """
    return pd.read_csv(
        path,
        dtype={
            "order_id": "int64",
            "customer_name": "string",
            "region": "string",
            "product": "string",
            "category": "string",
            "quantity": "int64",
            "unit_price": "float64",
            "total_amount": "float64",
        },
        parse_dates=["order_date"],
    )


def summarize_stdlib(rows: list[dict]) -> None:
    """Print a brief summary of rows loaded via stdlib."""
    total = sum(r["total_amount"] for r in rows)
    print(f"  stdlib csv.DictReader — {len(rows)} rows, sum(total_amount)={total:,.2f}")
    print(f"  first row: {rows[0]}")


def summarize_pandas(df: pd.DataFrame) -> None:
    """Print a brief summary of a DataFrame loaded via pandas."""
    print(f"  pandas.read_csv — shape={df.shape}, sum(total_amount)={df['total_amount'].sum():,.2f}")
    print(f"  dtypes:\n{df.dtypes.to_string()}")
