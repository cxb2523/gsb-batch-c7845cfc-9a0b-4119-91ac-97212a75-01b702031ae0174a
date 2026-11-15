"""
Fault-isolation tests for PDFScraper.monthly_iterator and
monthly_iterator_multi_column_frame, driven by an in-memory reader stub.

These tests do not touch fitz or the statement directory: every outcome
(success / missing file / parse failure / extraction failure) is injected
through the replaceable ``pdf_text_reader`` seam. Real-PDF coverage lives in
test_real_pdf_pipeline.py.
"""

from datetime import datetime

import pandas as pd
import pytest
from dateutil.relativedelta import relativedelta

START_STATEMENT = "2024-January.pdf"
START_LABEL = "2024-January"
EXPECTED_MONTHS = 60 + 1  # five years inclusive of the starting month
EXPECTED_MONTHS_EXTRA = EXPECTED_MONTHS + 1


def expected_statement_paths(add_additional_month: bool) -> "list[tuple[str, str]]":
    """Mirror the production month-walk so tests assert against independently computed labels."""
    start_date = datetime.strptime(START_LABEL, "%Y-%B")
    additional = 2 if add_additional_month else 1
    paths = []
    for months in range((12 * 5) + additional):
        label = (start_date - relativedelta(months=months)).strftime("%Y-%B")
        paths.append((f"{label}.pdf", label))
    return paths


def month_value_series(month_label: str):
    """Deterministic value derived solely from the month label, used to detect misalignment.

    Returned as a one-element list so that assigning the column works identically on
    pandas 1.4 (project-pinned) and pandas 3.x.
    """
    date = datetime.strptime(month_label, "%Y-%B")
    return [date.year * 100 + date.month]


def register_all_readable(backend, add_additional_month: bool = False):
    for path, label in expected_statement_paths(add_additional_month):
        backend.set_statement(path, {1: f"content for {label}"})


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_all_months_succeed_single_column(statement_backend):
    backend = statement_backend
    register_all_readable(backend)
    scraper = backend.new_scraper(START_STATEMENT)

    def extract():
        # Value is derived from the statement currently swapped in.
        label = scraper.currently_opened_statement.split(".")[0]
        return month_value_series(label)

    result = scraper.monthly_iterator(extract, add_additional_month=False)

    assert len(result.period_results) == EXPECTED_MONTHS
    assert all(period.successful for period in result.period_results)
    assert result.missing_periods == []
    assert result.parse_error_periods == []
    assert result.failed_periods == []
    assert result.all_periods_failed is False
    assert result.restoration_error is None

    # Columns are the month labels, in iteration order.
    expected_labels = [label for _, label in expected_statement_paths(False)]
    assert list(result.data.columns) == expected_labels

    # Every value must match the month label of its own column (no positional shift).
    for label in expected_labels:
        assert result.data[label].iloc[0] == month_value_series(label)[0]

    # The scraper must end on the statement it started with, not the last attempted file.
    assert scraper.currently_opened_statement == START_STATEMENT
    last_attempted = expected_labels[-1] + ".pdf"
    assert scraper.currently_opened_statement != last_attempted


def test_add_additional_month_extends_range(statement_backend):
    backend = statement_backend
    register_all_readable(backend, add_additional_month=True)
    scraper = backend.new_scraper(START_STATEMENT)

    result = scraper.monthly_iterator(lambda: 1, add_additional_month=True)

    assert len(result.period_results) == EXPECTED_MONTHS_EXTRA
    assert list(result.data.columns) == [label for _, label in expected_statement_paths(True)]
    assert scraper.currently_opened_statement == START_STATEMENT


def test_multi_column_frame_happy_path_alignment(statement_backend):
    backend = statement_backend
    register_all_readable(backend)
    scraper = backend.new_scraper(START_STATEMENT)
    columns = ["Month Number", "Year Value"]

    def extract_row():
        label = scraper.currently_opened_statement.split(".")[0]
        date = datetime.strptime(label, "%Y-%B")
        return date.month, date.year

    result = scraper.monthly_iterator_multi_column_frame(
        extract_row, add_additional_month=False, columns=columns
    )

    expected_labels = [label for _, label in expected_statement_paths(False)]
    assert list(result.data.index) == expected_labels
    assert list(result.data.columns) == columns

    for label in expected_labels:
        date = datetime.strptime(label, "%Y-%B")
        assert result.data.loc[label, "Month Number"] == date.month
        assert result.data.loc[label, "Year Value"] == date.year

    assert all(period.successful for period in result.period_results)
    assert scraper.currently_opened_statement == START_STATEMENT


# ---------------------------------------------------------------------------
# Isolation: missing + parse failures mixed with successes
# ---------------------------------------------------------------------------

def test_missing_and_parse_failures_are_isolated_single_column(statement_backend):
    backend = statement_backend
    all_paths = expected_statement_paths(False)
    register_all_readable(backend)

    missing_path, missing_label = all_paths[7]
    corrupt_path, corrupt_label = all_paths[13]
    backend.set_missing(missing_path)
    backend.set_corrupt(corrupt_path)

    scraper = backend.new_scraper(START_STATEMENT)

    def extract():
        label = scraper.currently_opened_statement.split(".")[0]
        return month_value_series(label)

    result = scraper.monthly_iterator(extract, add_additional_month=False)

    # No exception, full period coverage, two recorded failures.
    assert len(result.period_results) == EXPECTED_MONTHS
    status_by_month = {period.month: period for period in result.period_results}

    missing_result = status_by_month[missing_label]
    assert missing_result.status == backend.status.MISSING
    assert missing_result.file_name == missing_path
    assert "was not found" in missing_result.error_message

    corrupt_result = status_by_month[corrupt_label]
    assert corrupt_result.status == backend.status.PARSE_ERROR
    assert corrupt_result.file_name == corrupt_path
    assert "ValueError" in corrupt_result.error_message

    assert [period.month for period in result.missing_periods] == [missing_label]
    assert [period.month for period in result.parse_error_periods] == [corrupt_label]
    assert len(result.successful_periods) == EXPECTED_MONTHS - 2
    assert result.all_periods_failed is False

    # Data frame contains only successful months, values still bound to their month.
    successful_labels = [
        label for _, label in all_paths
        if label not in {missing_label, corrupt_label}
    ]
    assert list(result.data.columns) == successful_labels
    for label in successful_labels:
        assert result.data[label].iloc[0] == month_value_series(label)[0]

    # Neighbouring columns around the holes prove no positional shift.
    prior_label = all_paths[6][1]
    next_label = all_paths[8][1]
    assert result.data[prior_label].iloc[0] == month_value_series(prior_label)[0]
    assert result.data[next_label].iloc[0] == month_value_series(next_label)[0]

    assert scraper.currently_opened_statement == START_STATEMENT
    assert result.restoration_error is None


def test_missing_and_parse_failures_are_isolated_multi_column(statement_backend):
    backend = statement_backend
    all_paths = expected_statement_paths(False)
    register_all_readable(backend)

    missing_path, missing_label = all_paths[0]   # newest month missing
    corrupt_path, corrupt_label = all_paths[-1]  # oldest month unparseable
    backend.set_missing(missing_path)
    backend.set_corrupt(corrupt_path)

    scraper = backend.new_scraper(START_STATEMENT)
    columns = ["Month Number", "Year Value"]

    def extract_row():
        label = scraper.currently_opened_statement.split(".")[0]
        date = datetime.strptime(label, "%Y-%B")
        return date.month, date.year

    result = scraper.monthly_iterator_multi_column_frame(
        extract_row, add_additional_month=False, columns=columns
    )

    successful_labels = [
        label for _, label in all_paths
        if label not in {missing_label, corrupt_label}
    ]
    assert list(result.data.index) == successful_labels
    assert list(result.data.columns) == columns

    # Spot-check rows immediately next to the holes.
    second_label = all_paths[1][1]
    second_last_label = all_paths[-2][1]
    assert result.data.loc[second_label, "Month Number"] == datetime.strptime(
        second_label, "%Y-%B"
    ).month
    assert result.data.loc[second_last_label, "Year Value"] == datetime.strptime(
        second_last_label, "%Y-%B"
    ).year

    statuses = {period.month: period.status for period in result.period_results}
    assert statuses[missing_label] == backend.status.MISSING
    assert statuses[corrupt_label] == backend.status.PARSE_ERROR

    # The starting statement itself is the missing month here. Restoring it therefore
    # fails through the same missing-file path and must be reported rather than raised;
    # swap_statement only updates the opened file after a successful read, so the
    # scraper stays on the last successfully opened statement.
    assert result.restoration_error is not None
    assert "FileNotFoundError" in result.restoration_error
    assert [period.month for period in result.period_results][0] == missing_label


def test_extraction_function_failure_is_parse_error_and_isolated(statement_backend):
    backend = statement_backend
    all_paths = expected_statement_paths(False)
    register_all_readable(backend)
    failing_label = all_paths[5][1]

    scraper = backend.new_scraper(START_STATEMENT)

    def extract():
        label = scraper.currently_opened_statement.split(".")[0]
        if label == failing_label:
            raise KeyError("section not present on this statement (missing page)")
        return month_value_series(label)

    result = scraper.monthly_iterator(extract, add_additional_month=False)

    status_by_month = {period.month: period for period in result.period_results}
    failed = status_by_month[failing_label]
    assert failed.status == backend.status.PARSE_ERROR
    assert "KeyError" in failed.error_message
    assert "missing page" in failed.error_message
    assert len(result.successful_periods) == EXPECTED_MONTHS - 1
    assert failing_label not in result.data.columns
    assert scraper.currently_opened_statement == START_STATEMENT


# ---------------------------------------------------------------------------
# Total failure
# ---------------------------------------------------------------------------

def test_all_months_missing_returns_empty_frame_without_raising(statement_backend):
    backend = statement_backend
    # Nothing registered: every read raises FileNotFoundError.
    scraper = backend.new_scraper(START_STATEMENT)

    result = scraper.monthly_iterator(lambda: 1, add_additional_month=False)

    assert isinstance(result.data, pd.DataFrame)
    assert result.data.empty
    assert len(result.period_results) == EXPECTED_MONTHS
    assert all(period.status == backend.status.MISSING for period in result.period_results)
    assert result.all_periods_failed is True
    # Failure list covers every attempted month, in order.
    assert [period.month for period in result.period_results] == [
        label for _, label in expected_statement_paths(False)
    ]


def test_all_months_parse_error_multi_column_returns_empty_typed_frame(statement_backend):
    backend = statement_backend
    for path, _label in expected_statement_paths(False):
        backend.set_corrupt(path)

    scraper = backend.new_scraper(START_STATEMENT)
    columns = ["Ending Value", "Beginning Value", "Cash Flow"]

    result = scraper.monthly_iterator_multi_column_frame(
        lambda: (1, 2, 3), add_additional_month=False, columns=columns
    )

    assert isinstance(result.data, pd.DataFrame)
    assert result.data.empty
    assert list(result.data.columns) == columns
    assert len(result.period_results) == EXPECTED_MONTHS
    assert all(period.status == backend.status.PARSE_ERROR for period in result.period_results)
    assert result.all_periods_failed is True
    assert scraper.currently_opened_statement == START_STATEMENT


# ---------------------------------------------------------------------------
# Restoration semantics
# ---------------------------------------------------------------------------

def test_scraper_restored_even_when_restatement_itself_fails(statement_backend):
    backend = statement_backend
    all_paths = expected_statement_paths(False)
    register_all_readable(backend)
    # Punch a hole somewhere in the middle.
    backend.set_missing(all_paths[20][0])
    scraper = backend.new_scraper(START_STATEMENT)

    result = scraper.monthly_iterator(lambda: 1, add_additional_month=False)

    # Restore target exists in the stub, so restoration succeeds cleanly.
    assert result.restoration_error is None
    assert scraper.currently_opened_statement == START_STATEMENT


def test_restoration_error_is_reported_not_raised(statement_backend):
    backend = statement_backend
    # No readers at all: every period fails AND swapping back to the original
    # statement also fails. The error must be captured rather than raised and the
    # data must still be returned.
    attempted_files = []
    original_reader = backend.stub_reader

    def tracking_reader(pdf_name):
        attempted_files.append(pdf_name)
        return original_reader(pdf_name)

    backend.module.pdf_text_reader = tracking_reader
    scraper = backend.new_scraper(START_STATEMENT)

    result = scraper.monthly_iterator(lambda: 1, add_additional_month=False)

    assert result.data.empty
    assert result.restoration_error is not None
    assert "FileNotFoundError" in result.restoration_error
    assert len(result.period_results) == EXPECTED_MONTHS
    # The restore still attempted to open the original statement, last of all.
    assert attempted_files[-1] == START_STATEMENT
    # 61 period attempts + 1 restoration attempt, all accounted for.
    assert len(attempted_files) == EXPECTED_MONTHS + 1


# ---------------------------------------------------------------------------
# Ordering / structure
# ---------------------------------------------------------------------------

def test_period_results_follow_iteration_order(statement_backend):
    backend = statement_backend
    all_paths = expected_statement_paths(False)
    register_all_readable(backend)
    backend.set_missing(all_paths[3][0])
    backend.set_corrupt(all_paths[9][0])
    scraper = backend.new_scraper(START_STATEMENT)

    result = scraper.monthly_iterator(lambda: 1, add_additional_month=False)

    assert [period.month for period in result.period_results] == [
        label for _, label in all_paths
    ]
    assert [period.file_name for period in result.period_results] == [
        path for path, _ in all_paths
    ]
    # Data columns preserve the same relative order with holes removed.
    holes = {all_paths[3][1], all_paths[9][1]}
    assert list(result.data.columns) == [
        label for _, label in all_paths if label not in holes
    ]


def test_result_properties_and_status_enum_values(statement_backend):
    backend = statement_backend
    all_paths = expected_statement_paths(False)
    register_all_readable(backend)
    backend.set_missing(all_paths[1][0])
    backend.set_corrupt(all_paths[2][0])
    scraper = backend.new_scraper(START_STATEMENT)

    result = scraper.monthly_iterator(lambda: 1, add_additional_month=False)

    assert backend.status.SUCCESS.value == "success"
    assert backend.status.MISSING.value == "missing"
    assert backend.status.PARSE_ERROR.value == "parse_error"

    assert len(result.successful_periods) == EXPECTED_MONTHS - 2
    assert len(result.failed_periods) == 2
    successful = result.successful_periods[0]
    assert successful.error_message is None
    assert successful.successful is True
    assert result.missing_periods[0].successful is False
    assert result.parse_error_periods[0].successful is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
