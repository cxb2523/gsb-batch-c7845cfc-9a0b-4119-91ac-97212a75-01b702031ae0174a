"""Data Vault 2.1 — Shared Hashing Utilities

Centralised hash functions used by both staging and raw vault layers.
All hash computation follows the automate-dv / Data Vault 2.1 standard:

  Algorithm :  SHA-256 (64-char hex string)
  Salt      :  Prepended to every hash input to prevent cross-system collisions
  Delimiter :  '||' separates concatenated business-key columns
  NULLs     :  Replaced with '^^' via COALESCE before hashing
  Casing    :  UPPER + TRIM applied to every input column

Usage in SDP pipeline files:
    from _dv_hash import hash_key, hash_diff, DV_LOAD_DTS, dv_record_source

Import works because SDP adds each pipeline's source directory to sys.path,
so files in the same transformations/ folder can import each other directly.
"""

from pyspark.sql import DataFrame
from pyspark.sql.functions import (
    coalesce,
    concat_ws,
    current_timestamp,
    lit,
    sha2,
    trim,
    upper,
)

# ── Constants ─────────────────────────────────────────────────────
HASH_SALT = "RESTAURANT_CHAIN"
HASH_DELIMITER = "||"
HASH_NULL_REPLACEMENT = "^^"


def _normalise(col_ref):
    """UPPER → TRIM → COALESCE(…, '^^') a single column reference."""
    return coalesce(upper(trim(col_ref)), lit(HASH_NULL_REPLACEMENT))


def hash_key(df: DataFrame, columns: list[str], alias: str) -> DataFrame:
    """Compute a salted SHA-256 hash key from one or more business-key columns.

    Parameters
    ----------
    df       : Source DataFrame.
    columns  : Business-key column names (order matters for composites).
    alias    : Name of the resulting hash-key column (e.g. 'hk_customer').

    Returns
    -------
    DataFrame with the new hash-key column appended.
    """
    from pyspark.sql.functions import col

    parts = [lit(HASH_SALT)] + [_normalise(col(c)) for c in columns]
    return df.withColumn(alias, sha2(concat_ws(HASH_DELIMITER, *parts), 256))


def hash_diff(df: DataFrame, columns: list[str], alias: str = "hash_diff") -> DataFrame:
    """Compute a SHA-256 hash-diff over descriptive / payload columns.

    Used by satellites to detect attribute changes between loads.
    Salt is intentionally omitted — hash_diff is internal to a single
    satellite and never needs cross-system collision resistance.

    Parameters
    ----------
    df       : Source DataFrame.
    columns  : Descriptive attribute column names (alphabetical order recommended).
    alias    : Name of the resulting column (default 'hash_diff').

    Returns
    -------
    DataFrame with the hash-diff column appended.
    """
    from pyspark.sql.functions import col

    parts = [_normalise(col(c)) for c in sorted(columns)]
    return df.withColumn(alias, sha2(concat_ws(HASH_DELIMITER, *parts), 256))


# ── Metadata helpers ──────────────────────────────────────────────

DV_LOAD_DTS = current_timestamp  # call as DV_LOAD_DTS() to get current_timestamp()


def dv_record_source(source_name: str):
    """Return a literal column for the record_source metadata field."""
    return lit(source_name)
