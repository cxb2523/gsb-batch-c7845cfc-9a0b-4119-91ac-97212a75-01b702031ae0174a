# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 29: Data Format Processing
Processes the same sales dataset through a full format tour:
  CSV (stdlib + pandas) -> JSON / JSONL -> pandas clean/transform -> Parquet

No GCP credentials required — all operations are local.
"""

import os
from pathlib import Path

from csv_handler import load_with_stdlib, load_with_pandas, summarize_stdlib, summarize_pandas
from json_handler import dataframe_to_json, dataframe_to_jsonl, read_json, stream_jsonl, read_jsonl_pandas
from transformer import clean_and_transform, aggregate_by_region, pivot_monthly_by_category
from parquet_handler import write_parquet, read_parquet, benchmark

DATA_DIR = Path(__file__).parent / "data"
CSV_PATH = DATA_DIR / "sales_data.csv"

OUT_DIR = Path(__file__).parent / "output"
JSON_PATH = OUT_DIR / "sales_data.json"
JSONL_PATH = OUT_DIR / "sales_data.jsonl"
PARQUET_PATH = OUT_DIR / "sales_data.parquet"


def main():
    print("=" * 60)
    print("Project 29: Data Format Processing")
    print("=" * 60)
    print(f"Source: {CSV_PATH}\n")

    OUT_DIR.mkdir(exist_ok=True)

    # --- Step 1: CSV loading --------------------------------------------------
    print("--- Step 1: CSV Loading ---")
    rows = load_with_stdlib(CSV_PATH)
    summarize_stdlib(rows)
    print()

    df_raw = load_with_pandas(CSV_PATH)
    summarize_pandas(df_raw)
    print()

    # Sanity: both loaders must agree
    assert len(rows) == len(df_raw), "Row count mismatch between loaders"
    print(f"  OK: both loaders agree on {len(rows)} rows\n")

    # --- Step 2: JSON / JSONL ------------------------------------------------
    print("--- Step 2: JSON / JSONL ---")
    n_json = dataframe_to_json(df_raw, JSON_PATH)
    back_json = read_json(JSON_PATH)
    print(f"  JSON  : wrote {n_json} records -> {JSON_PATH.name} ({JSON_PATH.stat().st_size:,} bytes)")
    print(f"          read back: {len(back_json)} records")

    n_jsonl = dataframe_to_jsonl(df_raw, JSONL_PATH)
    print(f"\n  JSONL : wrote {n_jsonl} lines  -> {JSONL_PATH.name} ({JSONL_PATH.stat().st_size:,} bytes)")

    streamed = sum(1 for _ in stream_jsonl(JSONL_PATH))
    print(f"          streamed back: {streamed} records (constant memory)")

    df_jsonl = read_jsonl_pandas(JSONL_PATH)
    print(f"          pandas.read_json(lines=True) -> shape {df_jsonl.shape}")
    assert len(df_jsonl) == n_jsonl, "JSONL roundtrip lost records"
    print("  OK: JSON and JSONL roundtrips intact\n")

    # --- Step 3: Pandas clean/transform --------------------------------------
    print("--- Step 3: Pandas Clean + Transform ---")
    mem_before = df_raw.memory_usage(deep=True).sum()
    print(f"  raw memory: {mem_before:,} bytes")

    df_clean = clean_and_transform(df_raw)
    mem_after = df_clean.memory_usage(deep=True).sum()
    print(f"  after category dtype: {mem_after:,} bytes ({mem_after/mem_before:.0%} of raw)")

    print("\n  Revenue by region:")
    by_region = aggregate_by_region(df_clean)
    print(by_region.round(2).to_string())

    print("\n  Monthly revenue by category:")
    monthly = pivot_monthly_by_category(df_clean)
    print(monthly.round(2).head(6).to_string())
    print()

    # --- Step 4: Parquet read/write ------------------------------------------
    print("--- Step 4: Parquet Read/Write ---")
    pq_size = write_parquet(df_clean, PARQUET_PATH)
    print(f"  Parquet written: {PARQUET_PATH.name} ({pq_size:,} bytes)")

    df_pq = read_parquet(PARQUET_PATH)
    assert len(df_pq) == len(df_clean), "Parquet roundtrip lost rows"
    print(f"  Read back: {len(df_pq)} rows — dtypes preserved (no re-inference)")

    print("\n  Benchmark (CSV vs Parquet):")
    bm = benchmark(CSV_PATH, PARQUET_PATH)
    print(f"  CSV     size: {bm['csv_size']:>10,} bytes")
    print(f"  Parquet size: {bm['parquet_size']:>10,} bytes ({bm['parquet_size']/bm['csv_size']:.0%} of CSV)")
    print(f"  CSV read     : {bm['csv_read_ms']:.2f} ms")
    print(f"  Parquet read : {bm['parquet_read_ms']:.2f} ms")
    print(f"  Parquet (2 cols, pruned): {bm['parquet_slim_ms']:.2f} ms — CSV cannot do this")

    print()
    print("=" * 60)
    print("Done. Output files in output/:")
    print(f"  {JSON_PATH.name}")
    print(f"  {JSONL_PATH.name}")
    print(f"  {PARQUET_PATH.name}")
    print("=" * 60)


if __name__ == "__main__":
    main()
