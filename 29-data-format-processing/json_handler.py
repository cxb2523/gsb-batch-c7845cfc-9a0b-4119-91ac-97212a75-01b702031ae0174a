# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
JSON vs JSONL handling — read, write, and convert.
JSONL (Newline-Delimited JSON) is the lake/streaming standard used by BigQuery
load jobs, Gemini batch prediction, and Dataflow. JSON is for configs/API payloads.
"""

import json
from pathlib import Path

import pandas as pd


def dataframe_to_json(df: pd.DataFrame, out_path: str | Path) -> int:
    """Serialize a DataFrame to a plain JSON array file.

    JSON is fine for configs and API payloads. For data files it has two
    structural problems: (1) the whole file must be read before yielding the
    first record, and (2) a single corrupt byte invalidates everything.

    Dates are serialized as ISO-8601 strings (JSON has no date type).

    Args:
        df: Source DataFrame.
        out_path: Output path for the .json file.

    Returns:
        Number of records written.
    """
    out_path = Path(out_path)
    df_copy = df.copy()
    if "order_date" in df_copy.columns:
        df_copy["order_date"] = df_copy["order_date"].dt.strftime("%Y-%m-%d")
    records = df_copy.to_dict(orient="records")
    out_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return len(records)


def dataframe_to_jsonl(df: pd.DataFrame, out_path: str | Path) -> int:
    """Serialize a DataFrame to JSONL (one JSON object per line).

    JSONL advantages over plain JSON:
      - Appendable and splittable (parallel BQ load jobs)
      - Streamable with constant memory (one line at a time)
      - One bad line loses one record, not the whole file
    BigQuery `bq load --source_format=NEWLINE_DELIMITED_JSON`, Gemini batch
    prediction input/output, and Dataflow all use JSONL natively.

    Args:
        df: Source DataFrame.
        out_path: Output path for the .jsonl file.

    Returns:
        Number of lines/records written.
    """
    out_path = Path(out_path)
    df_copy = df.copy()
    if "order_date" in df_copy.columns:
        df_copy["order_date"] = df_copy["order_date"].dt.strftime("%Y-%m-%d")
    records = df_copy.to_dict(orient="records")
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return len(records)


def read_json(path: str | Path) -> list[dict]:
    """Read a plain JSON array file into a list of dicts."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def stream_jsonl(path: str | Path):
    """Stream a JSONL file line-by-line (constant memory).

    Yields:
        One dict per line (record).
    """
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def read_jsonl_pandas(path: str | Path) -> pd.DataFrame:
    """Read a JSONL file into a pandas DataFrame.

    The lines=True flag is the one people forget — without it pandas
    tries to parse the JSONL as a JSON object and raises a ValueError.
    """
    return pd.read_json(path, lines=True)
