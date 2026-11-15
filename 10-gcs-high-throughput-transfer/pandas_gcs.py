# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Read CSV directly from GCS into pandas via gs:// URI.

pandas delegates gs:// URLs to gcsfs, which picks up Application Default
Credentials automatically — no explicit GCS client code required. This is
the zero-boilerplate path for analytics scripts and notebooks that need to
read GCS data without a download step.

Requires: pip install pandas gcsfs

gcsfs is a lazily-opened filesystem layer — the first read authenticates and
caches credentials. For parallel reads in multi-process environments, create
a fresh GCSFileSystem per process.
"""

import os

import pandas as pd

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")

GCS_PATH = f"gs://{GCS_BUCKET}/data/sales_data.csv"


def read_csv_from_gcs(gcs_path: str = GCS_PATH) -> pd.DataFrame:
    """
    Read a CSV from a gs:// path directly into a DataFrame.
    Authentication is handled by gcsfs + Application Default Credentials.
    """
    print(f"\n--- pandas.read_csv over gs:// ---")
    print(f"Reading: {gcs_path}")
    df = pd.read_csv(gcs_path)
    print(f"Shape   : {df.shape[0]} rows x {df.shape[1]} cols")
    print(f"Columns : {list(df.columns)}")
    return df


def summarize_dataframe(df: pd.DataFrame) -> None:
    """Print a quick exploratory summary of the DataFrame."""
    print("\n--- First 5 rows ---")
    print(df.head().to_string(index=False))

    numeric_cols = df.select_dtypes("number").columns.tolist()
    categorical_cols = df.select_dtypes(exclude="number").columns.tolist()

    if categorical_cols and numeric_cols:
        group_col = categorical_cols[0]
        value_col = numeric_cols[-1]  # last numeric = total_amount for sales data
        print(f"\n--- Sum of '{value_col}' by '{group_col}' ---")
        agg = (
            df.groupby(group_col)[value_col]
            .sum()
            .sort_values(ascending=False)
            .head(10)
        )
        print(agg.to_string())

        # Additional: breakdown by category if present
        if "category" in df.columns and "total_amount" in df.columns:
            print("\n--- Revenue by category ---")
            cat_agg = (
                df.groupby("category")["total_amount"]
                .agg(["sum", "count", "mean"])
                .rename(columns={"sum": "revenue", "count": "orders", "mean": "avg_order"})
                .sort_values("revenue", ascending=False)
            )
            print(cat_agg.to_string())
    elif numeric_cols:
        print("\n--- Numeric summary ---")
        print(df[numeric_cols].describe().to_string())


def run_pandas_gcs_demo() -> pd.DataFrame:
    """Read the sales CSV from GCS and run a quick aggregation."""
    df = read_csv_from_gcs()
    summarize_dataframe(df)
    return df
