"""Acceptance tests for the collect -> clean -> report pipeline.

Covers:
  1. Normal data inside the 7-day window (CSV and SQLite sources)
  2. Empty 7-day window (no chart/PDF, header-only CSV, stale artifacts removed)
  3. Rows with unparseable invoice_date (dropped + counted, not fatal)
  4. Collection failure (reported via ReportResult, still logged to log.txt)

Run from the repo root:
    python tests/test_pipeline.py
"""
import os
import sys
import tempfile

os.environ.setdefault("MPLBACKEND", "Agg")

# Windows consoles default to GBK; the pipeline logs emoji to stdout.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import pandas as pd  # noqa: E402

from src import report  # noqa: E402
from src.report import (  # noqa: E402
    generate_and_send_report,
    STATUS_SUCCESS,
    STATUS_EMPTY_WINDOW,
    STATUS_ERROR,
    SUMMARY_CSV_PATH,
)
from config.settings import CHART_PATH, PDF_PATH  # noqa: E402

CURRENT_DATE = "2026-11-16"
COLUMNS = ["invoice_no", "customer_id", "category", "quantity", "price",
           "invoice_date", "shopping_mall"]

_emails_sent = []


def _fake_send_email_report(**kwargs):
    _emails_sent.append(kwargs)


report.send_email_report = _fake_send_email_report  # monkeypatch, no SMTP


def make_rows(dates, customer="C1"):
    return [[f"I{i}", customer, "Books", 2, 10.0 + i, d, "Test Mall"]
            for i, d in enumerate(dates)]


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pd.DataFrame(rows, columns=COLUMNS).to_csv(path, index=False)


def write_sqlite(path, rows):
    import sqlite3
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    pd.DataFrame(rows, columns=COLUMNS).to_sql("sales", conn,
                                               if_exists="replace",
                                               index=False)
    conn.close()


def read_summary():
    with open(SUMMARY_CSV_PATH, encoding="utf-8") as f:
        return f.read()


class workspace:
    """chdir into a fresh temp dir for the duration of a scenario."""

    def __enter__(self):
        self._prev = os.getcwd()
        self._tmp = tempfile.mkdtemp(prefix="report_test_")
        os.chdir(self._tmp)
        _emails_sent.clear()
        return self

    def __exit__(self, *exc):
        os.chdir(self._prev)
        return False


def test_normal_csv():
    with workspace():
        write_csv("data/sales.csv", make_rows(
            ["10-11-2026", "12-11-2026", "15-11-2026"], customer="C1")
            + make_rows(["11-11-2026"], customer="C2"))
        result = generate_and_send_report(
            source_type="csv", filepath="data/sales.csv",
            current_date=CURRENT_DATE)
        assert result.status == STATUS_SUCCESS, result
        assert result.valid_rows == 4 and result.dropped_rows == 0, result
        assert result.window_rows == 4, result
        summary = pd.read_csv(SUMMARY_CSV_PATH)
        assert list(summary.columns) == ["customer_id", "sale_amount"]
        assert len(summary) == 2  # two customers
        assert os.path.exists(CHART_PATH), "chart missing"
        assert os.path.exists(PDF_PATH), "PDF missing"
        assert len(_emails_sent) == 1, "email not sent"
    print("✅ normal 7-day data (csv): success, CSV+chart+PDF+email produced")


def test_empty_window_removes_stale_artifacts():
    with workspace():
        write_csv("data/sales.csv", make_rows(
            ["01-01-2026", "05-03-2026", "20-10-2026"]))
        # Stale outputs from a previous run
        os.makedirs("output", exist_ok=True)
        for p in (CHART_PATH, PDF_PATH):
            with open(p, "w") as f:
                f.write("stale")
        result = generate_and_send_report(
            source_type="csv", filepath="data/sales.csv",
            current_date=CURRENT_DATE)
        assert result.status == STATUS_EMPTY_WINDOW, result
        assert result.valid_rows == 3 and result.window_rows == 0, result
        assert read_summary() == "customer_id,sale_amount\n", read_summary()
        assert not os.path.exists(CHART_PATH), "stale chart not removed"
        assert not os.path.exists(PDF_PATH), "stale PDF not removed"
        assert len(_emails_sent) == 0, "email should not be sent"
    print("✅ empty window: header-only CSV, stale chart/PDF removed, no email")


def test_invalid_dates_dropped_and_counted():
    with workspace():
        rows = make_rows(["12-11-2026", "14-11-2026"])
        rows += make_rows(["not-a-date", "2026/11/12", ""], customer="C2")
        write_csv("data/sales.csv", rows)
        result = generate_and_send_report(
            source_type="csv", filepath="data/sales.csv",
            current_date=CURRENT_DATE)
        assert result.status == STATUS_SUCCESS, result
        assert result.valid_rows == 2, result
        assert result.dropped_rows == 3, result
        summary = pd.read_csv(SUMMARY_CSV_PATH)
        assert list(summary["customer_id"]) == ["C1"]
    print("✅ invalid dates: dropped+counted, run still succeeds")


def test_all_dates_invalid_is_empty_window():
    with workspace():
        write_csv("data/sales.csv", make_rows(["garbage", "", "32-13-2026"]))
        result = generate_and_send_report(
            source_type="csv", filepath="data/sales.csv",
            current_date=CURRENT_DATE)
        assert result.status == STATUS_EMPTY_WINDOW, result
        assert result.valid_rows == 0 and result.dropped_rows == 3, result
        assert read_summary() == "customer_id,sale_amount\n"
    print("✅ all dates invalid: empty_window with dropped count")


def test_normal_sqlite_same_cleaning():
    with workspace():
        rows = make_rows(["10-11-2026", "bad-date", "16-11-2026"])
        write_sqlite("data/sales_data.db", rows)
        result = generate_and_send_report(
            source_type="sqlite", current_date=CURRENT_DATE)
        assert result.status == STATUS_SUCCESS, result
        assert result.valid_rows == 2 and result.dropped_rows == 1, result
        assert os.path.exists(CHART_PATH) and os.path.exists(PDF_PATH)
    print("✅ normal 7-day data (sqlite): same shared cleaning applied")


def test_collection_error_reported_not_swallowed():
    with workspace():
        result = generate_and_send_report(
            source_type="csv", filepath="data/does_not_exist.csv",
            current_date=CURRENT_DATE)
        assert result.status == STATUS_ERROR, result
        assert result.error and "does_not_exist" in result.error, result
        with open("log.txt", encoding="utf-8") as f:
            log_content = f.read()
        assert "ERROR" in log_content and "Traceback" in log_content
    print("✅ collection failure: error result returned AND logged to log.txt")


if __name__ == "__main__":
    test_normal_csv()
    test_empty_window_removes_stale_artifacts()
    test_invalid_dates_dropped_and_counted()
    test_all_dates_invalid_is_empty_window()
    test_normal_sqlite_same_cleaning()
    test_collection_error_reported_not_swallowed()
    print("\nAll acceptance scenarios passed.")
