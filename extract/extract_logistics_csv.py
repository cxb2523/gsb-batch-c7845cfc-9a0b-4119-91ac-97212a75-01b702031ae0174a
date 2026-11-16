import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()

# The Windows console defaults to a legacy code page (e.g. GBK) that
# can't encode the ✅/❌ status markers — force UTF-8 output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

# Directory that the logistics provider drops daily CSV files into.
# Override with LOGISTICS_CSV_DIR (useful for testing).
CSV_DIR = Path(os.getenv("LOGISTICS_CSV_DIR", "data/csv"))

# The columns the logistics provider's spec says every CSV must have.
# A file that doesn't have these is rejected (recorded as a failure)
# rather than loaded partially.
EXPECTED_COLUMNS = [
    "shipment_id",
    "order_reference",
    "dispatch_date",
    "delivery_date",
    "carrier",
    "status",
    "weight_kg",
]


def get_wh_connection():
    """
    create and return a warehouse connection.
    """
    return psycopg2.connect(
        host=os.getenv("WAREHOUSE_DB_HOST"),
        port=os.getenv("WAREHOUSE_DB_PORT"),
        dbname=os.getenv("WAREHOUSE_DB_NAME"),
        user=os.getenv("WAREHOUSE_DB_USER"),
        password=os.getenv("WAREHOUSE_DB_PASS")
    )


def ensure_shipments_table(wh_conn):
    """
    Unlike the Postgres extraction where we inferred columns from the source,
    here we know exactly what columns the CSV has (the logistics provider
    gave us a spec document). So we hardcode the CREATE TABLE statement.

    Everything is stored as TEXT — raw means raw, no type enforcement yet.

    Batch-tracking columns (they don't exist in the CSV, we add them):
      source_file — which CSV file this row came from
      batch_id    — SHA-256 of the file's contents. This is what makes a
                    load idempotent: the same file name can reappear with
                    different contents (a new daily drop), and the same
                    contents can reappear under a different name. The
                    content hash identifies the actual data loaded.
      loaded_at   — when this batch was loaded

    The ALTER ... ADD COLUMN IF NOT EXISTS statements upgrade tables that
    were created by an older version of this script, so nobody has to
    drop and rebuild raw.shipments by hand.
    """
    cur = wh_conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS raw.shipments (
            shipment_id        TEXT,
            order_reference    TEXT,
            dispatch_date      TEXT,
            delivery_date      TEXT,
            carrier            TEXT,
            status             TEXT,
            weight_kg          TEXT,
            source_file        TEXT,
            batch_id           TEXT,
            loaded_at          TIMESTAMPTZ
        )
    """)
    # Smooth in-place upgrade for tables created before the batch
    # columns existed — no manual rebuild required.
    cur.execute("ALTER TABLE raw.shipments ADD COLUMN IF NOT EXISTS source_file TEXT")
    cur.execute("ALTER TABLE raw.shipments ADD COLUMN IF NOT EXISTS batch_id TEXT")
    cur.execute("ALTER TABLE raw.shipments ADD COLUMN IF NOT EXISTS loaded_at TIMESTAMPTZ")
    wh_conn.commit()
    cur.close()


def compute_batch_id(file_path):
    """
    SHA-256 over the raw file bytes. Two files with the same contents are
    the same batch, even if they have different names; two files with
    the same name but different contents are different batches.
    """
    with open(file_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def already_loaded(cur, batch_id):
    """
    Idempotency check: has this exact batch (file contents) been loaded?
    We key on batch_id, NOT on the file name — different daily files can
    share a name, and a re-edited file must be picked up again.
    """
    cur.execute(
        "SELECT 1 FROM raw.shipments WHERE batch_id = %s LIMIT 1",
        (batch_id,)
    )
    return cur.fetchone() is not None


def extract_csv(file_path, wh_conn):
    """
    Reads a single CSV file and loads all its rows into raw.shipments.

    Returns "loaded" or "skipped". Raises on any read/insert error —
    the caller catches it, rolls back, and moves on to the next file.
    """
    file_path = Path(file_path)
    print(f"  Reading file: {file_path}")

    batch_id = compute_batch_id(file_path)

    # pd.read_csv() reads the CSV file into a DataFrame.
    # dtype=str means "treat every column as a string, don't try to
    # infer types automatically." We want raw text — no automatic
    # conversion of dates or numbers, because pandas might silently
    # misinterpret the messy formats in this file.
    # keep_default_na=False means empty cells become empty strings ""
    # rather than NaN (Not a Number), which is easier to handle later.
    df = pd.read_csv(file_path, dtype=str, keep_default_na=False)

    # Strip whitespace from column names, in case the CSV has
    # " shipment_id" with a leading space (a surprisingly common issue)
    df.columns = df.columns.str.strip()

    # Reject files that don't match the provider's spec — better to fail
    # this file loudly than to load garbage into the raw layer.
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing expected columns: {', '.join(missing)}")

    # Metadata columns. source_file is the path relative to the drop
    # directory so same-named files in different subfolders stay
    # distinguishable. loaded_at is one timestamp shared by the whole
    # batch, so all rows of a load are recognisably one batch.
    try:
        source_file = str(file_path.relative_to(CSV_DIR))
    except ValueError:
        source_file = file_path.name
    df["source_file"] = source_file
    df["batch_id"] = batch_id
    df["loaded_at"] = datetime.now(timezone.utc)

    cur = wh_conn.cursor()

    # We DON'T truncate here like we did for the Postgres tables.
    # Why? Because there are multiple CSV files (one per day), and we
    # want to keep all of them. Instead we load each file once and let
    # the staging layer handle deduplication. The batch_id check makes
    # re-running this script safe: already-loaded contents are skipped.
    if already_loaded(cur, batch_id):
        print(f"  ⏭️  Already loaded — skipping {source_file} (batch {batch_id[:12]}…)")
        cur.close()
        return "skipped"

    # Load each row into the warehouse. Explicit column list (not
    # positional VALUES) so the insert keeps working regardless of the
    # physical column order of older/newer table definitions.
    records = [tuple(row) for row in df.values]
    col_sql = ", ".join(df.columns)
    placeholders = ", ".join(["%s"] * len(df.columns))
    cur.executemany(
        f"INSERT INTO raw.shipments ({col_sql}) VALUES ({placeholders})",
        records
    )
    wh_conn.commit()

    print(f"  ✅ Loaded {len(df)} rows from {source_file} (batch {batch_id[:12]}…)")
    cur.close()
    return "loaded"


if __name__ == "__main__":
    print("Starting logistics CSV extraction...")

    wh_conn = get_wh_connection()
    ensure_shipments_table(wh_conn)

    # Discover every CSV in the drop directory (including subfolders —
    # the provider may organise drops as data/csv/2024-01-15/shipment.csv,
    # which is exactly why the file NAME alone can't identify a batch).
    csv_files = sorted(CSV_DIR.rglob("*.csv")) if CSV_DIR.is_dir() else []

    if not csv_files:
        print(f"  No CSV files found in {CSV_DIR}")

    # Process files independently: one bad file must not take down the
    # whole run. Failures are collected and reported at the end via the
    # exit code, while successfully loaded files stay committed.
    failures = []
    loaded = 0
    skipped = 0
    for f in csv_files:
        try:
            result = extract_csv(f, wh_conn)
            if result == "loaded":
                loaded += 1
            else:
                skipped += 1
        except Exception as e:
            # Roll back whatever this file partially inserted so the
            # table never holds half a batch, then keep going.
            wh_conn.rollback()
            failures.append((f, e))
            print(f"  ❌ Failed to load {f}: {e}")

    wh_conn.close()

    print(f"\nSummary: {loaded} loaded, {skipped} skipped, {len(failures)} failed")
    for f, e in failures:
        print(f"  FAILED: {f} — {e}")

    if failures:
        print("\nLogistics CSV extraction finished WITH FAILURES.")
        sys.exit(1)

    print("\nLogistics CSV extraction complete.")
    sys.exit(0)
