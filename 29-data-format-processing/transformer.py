# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Pandas cleaning and transformation pipeline.
The standard cleaning pass you run on almost every raw extract before loading
to BigQuery or writing to Parquet.
"""

import pandas as pd


def clean_and_transform(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the standard cleaning pipeline to the sales DataFrame.

    Steps:
      1. Parse/validate order_date (already parsed if loaded with parse_dates)
      2. Derive order_month and order_quarter for time-series grouping
      3. Deduplicate on business key (order_id)
      4. Fill missing values explicitly per-column (blanket fillna(0) is how
         silent data bugs are born)
      5. Downcast string columns with low cardinality to category dtype —
         routinely a 5-10x memory reduction on wide multi-GB frames

    Args:
        df: Raw DataFrame from csv_handler.load_with_pandas().

    Returns:
        Cleaned, transformed DataFrame ready for Parquet/BQ load.
    """
    df = df.copy()

    # 1. Ensure order_date is datetime
    if not pd.api.types.is_datetime64_any_dtype(df["order_date"]):
        df["order_date"] = pd.to_datetime(df["order_date"])

    # 2. Derived time columns — pre-compute so downstream SQL groupbys are trivial
    df["order_month"] = df["order_date"].dt.to_period("M").astype(str)
    df["order_quarter"] = df["order_date"].dt.to_period("Q").astype(str)

    # 3. Deduplicate on business key; keep="first" assumes recency ordering
    before = len(df)
    df = df.drop_duplicates(subset=["order_id"], keep="first")
    removed = before - len(df)
    if removed:
        print(f"  dedupe: removed {removed} duplicate order_id rows ({before} -> {len(df)})")

    # 4. Missing value handling — explicit per-column
    null_report = df.isna().sum()
    nulls = null_report[null_report > 0]
    if len(nulls):
        print(f"  nulls detected:\n{nulls.to_string()}")
    df["region"] = df["region"].fillna("UNKNOWN")
    df["quantity"] = df["quantity"].fillna(0).astype("int64")
    df["total_amount"] = df["total_amount"].fillna(df["quantity"] * df["unit_price"])

    # 5. Category dtype for low-cardinality string columns
    for col in ("region", "category", "product"):
        df[col] = df[col].astype("category")

    return df


def aggregate_by_region(df: pd.DataFrame) -> pd.DataFrame:
    """Group by region and compute orders, revenue, and average order value."""
    return (
        df.groupby("region", observed=True)
        .agg(
            orders=("order_id", "count"),
            revenue=("total_amount", "sum"),
            avg_order=("total_amount", "mean"),
        )
        .sort_values("revenue", ascending=False)
    )


def pivot_monthly_by_category(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot monthly revenue by product category."""
    return (
        df.groupby(["order_month", "category"], observed=True)["total_amount"]
        .sum()
        .unstack(fill_value=0)
    )
