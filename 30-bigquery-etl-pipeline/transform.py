# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Transform phase — data cleaning and shaping before BigQuery load.
Do light, schema-shaping transforms here; push heavy aggregation into BigQuery
after load (ELT pattern) — BQ slots are cheaper and faster for anything beyond
a few GB.
"""

import pandas as pd


def clean_for_bq(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and shape the raw DataFrame for BigQuery ingestion.

    Transformations applied:
      - Drop duplicate order_ids (keep first, assumes recency ordering)
      - Convert order_date datetime64 -> Python date (maps to BQ DATE, not TIMESTAMP)
      - Normalize region: fill nulls with UNKNOWN, uppercase
      - Recompute total_amount where missing (quantity * unit_price)

    Args:
        df: Raw DataFrame from extract.load_csv().

    Returns:
        Cleaned DataFrame ready for Parquet staging and BQ load.
    """
    df = df.copy()

    # Deduplicate on business key
    before = len(df)
    df = df.drop_duplicates(subset=["order_id"])
    if len(df) < before:
        print(f"  dedupe: {before} -> {len(df)} rows (removed {before - len(df)} dupes)")

    # Date type: datetime64 -> Python date -> BigQuery DATE (not TIMESTAMP)
    df["order_date"] = df["order_date"].dt.date

    # Region normalization
    df["region"] = df["region"].fillna("UNKNOWN").str.upper()

    # Recompute missing amounts
    df["total_amount"] = df["total_amount"].fillna(df["quantity"] * df["unit_price"])

    return df


def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add derived analytical columns useful for BigQuery partitioning/clustering.

    Note: order_date must still be datetime-compatible here. If it's already
    a Python date object, convert back for period extraction.

    Args:
        df: Cleaned DataFrame (order_date as datetime or date).

    Returns:
        DataFrame with order_month and order_quarter columns added.
    """
    df = df.copy()
    # Ensure datetime for period extraction
    dt_series = pd.to_datetime(df["order_date"])
    df["order_month"] = dt_series.dt.to_period("M").astype(str)
    df["order_quarter"] = dt_series.dt.to_period("Q").astype(str)
    return df


def summary_stats(df: pd.DataFrame) -> None:
    """Print a quick summary of the transformed DataFrame."""
    print(f"  shape: {df.shape}")
    print(f"  total_amount sum: {df['total_amount'].sum():,.2f}")
    print(f"  date range: {df['order_date'].min()} -> {df['order_date'].max()}")
    print(f"  regions: {sorted(df['region'].unique().tolist())}")
