"""
End-to-end monthly read-back tests against real on-disk PDFs.

Unlike test_monthly_iterator_stub.py, these tests exercise the production
``_read_pdf`` / fitz code path: PyMuPDF generates genuine (tiny) PDFs in a
temporary statement directory, the FileManagement stub is pointed at that
directory, and the replaceable reader seam is restored to the real reader.

Covered scenarios per the task requirements:
  * normal: valid PDFs for most months, values read through fitz;
  * missing (缺页): months whose PDF simply does not exist on disk;
  * parse failure (解析失败): a file that exists but is not a valid PDF.
"""

from datetime import datetime

import pytest
from dateutil.relativedelta import relativedelta

import PythonScripts.ScrapingScripts.FileManagement as FileManagement
from PythonScripts.ScrapingScripts.PDFScraper import (
    MonthlyReadStatus,
    _read_pdf,
    pdf_scraper,  # imported to ensure module-level instantiation works too
)

START_STATEMENT = "2024-January.pdf"
START_LABEL = "2024-January"
EXPECTED_MONTHS = 61

fitz = pytest.importorskip("fitz")


def statement_labels(add_additional_month: bool = False):
    start_date = datetime.strptime(START_LABEL, "%Y-%B")
    additional = 2 if add_additional_month else 1
    return [
        (start_date - relativedelta(months=months)).strftime("%Y-%B")
        for months in range((12 * 5) + additional)
    ]


def write_real_pdf(directory, name: str, text: str) -> None:
    document = fitz.Document()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(str(directory / name))
    document.close()


@pytest.fixture()
def real_pdf_environment(statement_backend, tmp_path, monkeypatch):
    """
    Point the FileManagement stub at a fresh directory of real PDFs and restore the
    genuine reader seam for the duration of the test (statement_backend's teardown
    restores the seam afterwards).
    """

    directory = tmp_path / "statements"
    directory.mkdir()
    monkeypatch.setattr(FileManagement, "statement_directory_path", str(directory))

    backend = statement_backend
    backend.module.pdf_text_reader = _read_pdf  # production path

    class Environment:
        def __init__(self):
            self.directory = directory
            self.module = backend.module
            self.new_scraper = backend.new_scraper

    return Environment()


def test_real_pdf_mixed_success_missing_and_corrupt(real_pdf_environment):
    env = real_pdf_environment
    labels = statement_labels()

    for label in labels:
        write_real_pdf(env.directory, f"{label}.pdf", f"Statement content for {label}")

    missing_label = labels[4]
    corrupt_label = labels[10]

    # Missing: remove the file entirely.
    (env.directory / f"{missing_label}.pdf").unlink()

    # Parse failure: overwrite with bytes fitz cannot open as a PDF.
    (env.directory / f"{corrupt_label}.pdf").write_bytes(b"\x00\x01\x02 not a pdf at all")

    scraper = env.new_scraper(START_STATEMENT)

    def extract():
        # Touch the genuinely parsed content so the test is not just swapping names.
        content = scraper.pdf[1]
        label = scraper.currently_opened_statement.split(".")[0]
        assert f"Statement content for {label}" in content
        date = datetime.strptime(label, "%Y-%B")
        # One-element list: column assignment behaves the same on pandas 1.4 and 3.x.
        return [date.year * 100 + date.month]

    result = scraper.monthly_iterator(extract, add_additional_month=False)

    # Full coverage, no exception, two isolated failures.
    assert len(result.period_results) == EXPECTED_MONTHS
    statuses = {period.month: period.status for period in result.period_results}
    assert statuses[missing_label] == MonthlyReadStatus.MISSING
    assert statuses[corrupt_label] == MonthlyReadStatus.PARSE_ERROR
    assert result.period_results[10].error_message  # fitz reports its own error type/message

    # Successful values remain bound to their own months despite the two holes.
    successful = [label for label in labels if label not in {missing_label, corrupt_label}]
    assert list(result.data.columns) == successful
    for label in successful:
        date = datetime.strptime(label, "%Y-%B")
        assert result.data[label].iloc[0] == date.year * 100 + date.month

    assert len(result.successful_periods) == EXPECTED_MONTHS - 2
    assert scraper.currently_opened_statement == START_STATEMENT
    assert result.restoration_error is None


def test_real_pdf_multi_column_with_missing_months(real_pdf_environment):
    env = real_pdf_environment
    labels = statement_labels()

    for index, label in enumerate(labels):
        write_real_pdf(env.directory, f"{label}.pdf", f"{label} page text")

    # Alternating missing months.
    missing = labels[2::6]
    for label in missing:
        (env.directory / f"{label}.pdf").unlink()

    scraper = env.new_scraper(START_STATEMENT)
    columns = ["Month Number", "Year"]

    def extract_row():
        label = scraper.currently_opened_statement.split(".")[0]
        date = datetime.strptime(label, "%Y-%B")
        return date.month, date.year

    result = scraper.monthly_iterator_multi_column_frame(
        extract_row, add_additional_month=False, columns=columns
    )

    remaining = [label for label in labels if label not in missing]
    assert list(result.data.index) == remaining
    assert len(result.period_results) == EXPECTED_MONTHS
    assert len(result.missing_periods) == len(missing)
    assert result.parse_error_periods == []

    for label in remaining:
        date = datetime.strptime(label, "%Y-%B")
        assert result.data.loc[label, "Month Number"] == date.month
        assert result.data.loc[label, "Year"] == date.year

    assert scraper.currently_opened_statement == START_STATEMENT


def test_real_pdf_all_months_fail_returns_empty_frame(real_pdf_environment):
    env = real_pdf_environment
    labels = statement_labels()

    # Every existing file is corrupt; one month has no file at all.
    for label in labels[1:]:
        (env.directory / f"{label}.pdf").write_bytes(b"garbage")
    # labels[0] -> no file at all (missing). The original statement needs no valid
    # file at restore time: restoration failure is captured, not raised.

    scraper = env.new_scraper(START_STATEMENT)

    result = scraper.monthly_iterator(lambda: 1, add_additional_month=False)

    assert result.data.empty
    assert len(result.period_results) == EXPECTED_MONTHS
    assert result.all_periods_failed is True
    assert len(result.missing_periods) == 1
    assert result.missing_periods[0].month == labels[0]
    assert len(result.parse_error_periods) == EXPECTED_MONTHS - 1
    # Restoration of the (also missing) original statement is reported, not raised.
    assert result.restoration_error is not None
    assert "FileNotFoundError" in result.restoration_error


def test_module_level_scraper_instance_is_constructible():
    # The import-time instantiation at the bottom of PDFScraper.py must keep working
    # through the genuine _read_pdf path (seed PDF created in conftest).
    assert pdf_scraper.currently_opened_statement == START_STATEMENT
    assert pdf_scraper.pdf[1]  # non-empty parsed page mapping
