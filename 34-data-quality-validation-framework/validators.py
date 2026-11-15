# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Data Quality Validators — all validation check functions.

Covers:
  - Schema validation: column set and dtype checks
  - Completeness: null checks on required columns
  - Range checks: quantity, unit_price, date range
  - Uniqueness: business key deduplication
  - Referential / consistency: region allow-list, derived-column cross-check

Each check calls the shared `record_check()` function which appends failures
to the supplied list and returns a dict for structured reporting.

Why gate here, not after load: a bad load into BigQuery is already an
incident — downstream views, scheduled queries, and dashboards consume it
within minutes. Failing the batch at the landing zone costs one re-run;
failing it in the warehouse costs a backfill plus stakeholder trust.
"""

import pandas as pd

# Contract for the fixture. In production this lives in config/source control
# so producers and consumers argue about ONE artifact, not tribal knowledge.
# Check type CATEGORIES, not exact dtype strings — pandas 3.x reads strings
# as `str` dtype (was `object`) and dates as datetime64[us] (was [ns]), so
# exact-string contracts silently break across pandas upgrades.
EXPECTED_COLUMNS = {
    "order_id": "integer",
    "order_date": "datetime",
    "customer_name": "string",
    "region": "string",
    "product": "string",
    "category": "string",
    "quantity": "integer",
    "unit_price": "float",
    "total_amount": "float",
}

DTYPE_CHECKS = {
    "integer": pd.api.types.is_integer_dtype,
    "float": pd.api.types.is_float_dtype,
    "string": lambda s: (
        pd.api.types.is_string_dtype(s) or pd.api.types.is_object_dtype(s)
    ),
    "datetime": pd.api.types.is_datetime64_any_dtype,
}

VALID_REGIONS = {"US-East", "US-West", "EU", "APAC"}
REQUIRED_NOT_NULL = ["order_id", "order_date", "total_amount"]


def record_check(name, condition, detail, failures):
    """
    Records a single check result.

    Args:
        name:      Human-readable check name.
        condition: Boolean — True = PASS, False = FAIL.
        detail:    Detail string shown only on failure.
        failures:  Mutable list — failed check strings are appended here.

    Returns:
        dict: {'name': str, 'status': 'PASS'|'FAIL', 'detail': str}
    """
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures.append(f"{name}: {detail}")
    return {"name": name, "status": status, "detail": detail if not condition else ""}


# ── Schema checks ──────────────────────────────────────────────────────────────

def check_column_set(df, failures):
    """Validates that the DataFrame has exactly the expected column set."""
    missing = set(EXPECTED_COLUMNS) - set(df.columns)
    extra = set(df.columns) - set(EXPECTED_COLUMNS)
    ok = not missing and not extra
    detail = f"missing={sorted(missing)} extra={sorted(extra)}"
    return record_check("column set matches", ok, detail, failures)


def check_dtypes(df, failures):
    """
    Validates the pandas dtype category for each expected column.
    Returns a list of check result dicts (one per column).
    """
    results = []
    for col, kind in EXPECTED_COLUMNS.items():
        if col in df.columns:
            ok = DTYPE_CHECKS[kind](df[col])
            detail = f"got {df[col].dtype}"
            results.append(
                record_check(f"dtype {col} is {kind}", ok, detail, failures)
            )
    return results


# ── Completeness checks ────────────────────────────────────────────────────────

def check_nulls(df, failures):
    """Checks that required columns contain no null values."""
    results = []
    for col in REQUIRED_NOT_NULL:
        n = int(df[col].isna().sum())
        ok = n == 0
        results.append(
            record_check(f"no nulls in {col}", ok, f"{n} null rows", failures)
        )
    return results


# ── Range checks ───────────────────────────────────────────────────────────────

def check_quantity_positive(df, failures):
    """Catches negative or zero quantity — unit bug signals."""
    ok = bool((df["quantity"] > 0).all())
    return record_check(
        "quantity > 0", ok, f"min={df['quantity'].min()}", failures
    )


def check_unit_price_positive(df, failures):
    """Catches zero or negative unit prices before they corrupt KPI averages."""
    ok = bool((df["unit_price"] > 0).all())
    return record_check(
        "unit_price > 0", ok, f"min={df['unit_price'].min()}", failures
    )


def check_order_date_not_future(df, failures):
    """Catches future-dated orders that may indicate timezone/clock skew."""
    ok = bool((df["order_date"] <= pd.Timestamp.now()).all())
    return record_check(
        "order_date not in future",
        ok,
        f"max={df['order_date'].max().date()}",
        failures,
    )


# ── Uniqueness checks ──────────────────────────────────────────────────────────

def check_order_id_unique(df, failures):
    """
    Validates that order_id is unique — duplicates make any MERGE nondeterministic.
    """
    dupes = int(df["order_id"].duplicated().sum())
    ok = dupes == 0
    return record_check("order_id unique", ok, f"{dupes} duplicate ids", failures)


# ── Consistency / referential checks ──────────────────────────────────────────

def check_region_valid(df, failures):
    """Validates that all region values are in the known allow-list."""
    bad = int((~df["region"].isin(VALID_REGIONS)).sum())
    ok = bad == 0
    return record_check(
        "region in known set", ok, f"{bad} unknown values", failures
    )


def check_total_amount_derived(df, failures):
    """
    Cross-column sanity: total_amount should equal quantity * unit_price (±0.01).
    Catches data-entry bugs and ETL transformation errors before downstream load.
    """
    calc = (df["quantity"] * df["unit_price"]).round(2)
    drift = int((abs(calc - df["total_amount"].round(2)) > 0.01).sum())
    ok = drift == 0
    return record_check(
        "total_amount == quantity*unit_price",
        ok,
        f"{drift} rows off",
        failures,
    )


# ── Full suite runner ──────────────────────────────────────────────────────────

def run_all_checks(df):
    """
    Runs the complete validation suite against `df`.

    Args:
        df: pandas DataFrame loaded from the source CSV.

    Returns:
        tuple[list[dict], list[str]]:
            - results: flat list of all check result dicts
            - failures: list of failed check description strings
    """
    failures = []
    results = []

    # Schema
    results.append(check_column_set(df, failures))
    results.extend(check_dtypes(df, failures))

    # Completeness
    results.extend(check_nulls(df, failures))

    # Ranges
    results.append(check_quantity_positive(df, failures))
    results.append(check_unit_price_positive(df, failures))
    results.append(check_order_date_not_future(df, failures))

    # Uniqueness
    results.append(check_order_id_unique(df, failures))

    # Consistency
    results.append(check_region_valid(df, failures))
    results.append(check_total_amount_derived(df, failures))

    return results, failures
