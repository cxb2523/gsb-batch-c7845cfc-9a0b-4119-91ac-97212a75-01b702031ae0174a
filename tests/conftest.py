"""
Pytest configuration for the PDFScraper monthly read-back tests.

The repository ships without ``config.json`` and without real Schwab statements, and
``FileManagement`` reads its configuration at import time. To make the scraping package
importable in isolation, a lightweight FileManagement stub is injected into
``sys.modules`` *before* ``PythonScripts.ScrapingScripts.PDFScraper`` is imported.

Two interchangeable verification backends are used by the tests:

1. A pure reader stub (``statement_backend`` fixture) that fully controls per-month
   success / missing / parse-error outcomes without any on-disk PDF.
2. Real local PDFs generated with PyMuPDF into a temporary statement directory, exercising
   the production ``_read_pdf`` / fitz code path end to end (see test_real_pdf_pipeline.py).
"""

import sys
import tempfile
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# One-time import scaffolding: FileManagement needs config.json, which is absent.
# A real (tiny) seed PDF is created with PyMuPDF so the module-level instantiation
# at the bottom of PDFScraper.py runs through the genuine _read_pdf/fitz path.
# ---------------------------------------------------------------------------

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="pdfscraper_tests_"))
_STATEMENTS_DIR = _SESSION_DIR / "1. Schwab Statements"
_STATEMENTS_DIR.mkdir()
_SEED_STATEMENT = "2024-January.pdf"


def _create_seed_pdf() -> None:
    import fitz

    document = fitz.Document()
    page = document.new_page()
    page.insert_text((72, 72), "Seed Schwab Statement 2024-January")
    document.save(str(_STATEMENTS_DIR / _SEED_STATEMENT))
    document.close()


_create_seed_pdf()

_STATE = {
    "most_recent_statement": _SEED_STATEMENT,
    "asset_types_as_shown_per_section": {},
    "fixed_income_etfs": [],
}


def _install_import_stubs() -> dict:
    file_management = types.ModuleType("PythonScripts.ScrapingScripts.FileManagement")
    file_management.statement_directory_path = str(_STATEMENTS_DIR)
    file_management.file_names = [_SEED_STATEMENT]
    file_management.asset_types_as_shown_per_section = _STATE["asset_types_as_shown_per_section"]
    file_management.config = {}

    def extract_schwab_statements() -> str:
        return _STATE["most_recent_statement"]

    def extract_fixed_income_etf_tickers() -> list:
        return list(_STATE["fixed_income_etfs"])

    def validate_statement_files() -> bool:
        return True

    file_management.extract_schwab_statements = extract_schwab_statements
    file_management.extract_fixed_income_etf_tickers = extract_fixed_income_etf_tickers
    file_management.validate_statement_files = validate_statement_files

    # Note: do NOT pre-register the parent packages. They are implicit namespace
    # packages (no __init__.py); a placeholder with an empty __path__ would shadow the
    # real directories and block submodule imports. Only FileManagement itself, whose
    # real module requires the absent config.json, is replaced with a stub.
    sys.modules["PythonScripts.ScrapingScripts.FileManagement"] = file_management
    return file_management


_file_management_stub = _install_import_stubs()

# Importing PDFScraper now succeeds: the module-level _read_pdf call opens the real
# seed PDF created above through the genuine fitz code path.
import PythonScripts.ScrapingScripts.PDFScraper as PDFScraper_module  # noqa: E402
from PythonScripts.ScrapingScripts.PDFScraper import (  # noqa: E402
    MonthlyReadStatus,
    PDFScraper,
)


@pytest.fixture()
def statement_backend(tmp_path):
    """
    Fully controllable reader-stub environment.

    Register months with ``set_statement`` (normal), leave them unregistered
    (missing file -> FileNotFoundError), or use ``set_corrupt`` /
    ``set_reader_error`` (parse failure). The production ``pdf_text_reader`` seam is
    replaced for the duration of the test and restored afterwards.
    """

    class StatementBackend:
        def __init__(self):
            self.directory = tmp_path / "1. Schwab Statements"
            self.directory.mkdir()
            self.module = PDFScraper_module
            self.pdf_scraper_cls = PDFScraper
            self.status = MonthlyReadStatus
            # name -> callable(pdf_name) returning Dict[int, str]
            self.readers = {}
            self.read_calls = []
            self.original_seam = PDFScraper_module.pdf_text_reader
            PDFScraper_module.pdf_text_reader = self.stub_reader

        def set_statement(self, name: str, pages):
            """Register a readable statement. ``pages`` is either a dict or a string."""
            if isinstance(pages, str):
                pages = {1: pages}

            def reader(pdf_name, _pages=dict(pages)):
                self.read_calls.append(pdf_name)
                return dict(_pages)

            self.readers[name] = reader

        def set_missing(self, name: str):
            self.readers.pop(name, None)

        def set_corrupt(self, name: str):
            """Simulate a file that exists but cannot be parsed (non-FileNotFound error)."""
            (self.directory / name).write_bytes(b"not a real pdf payload")

            def reader(pdf_name):
                self.read_calls.append(pdf_name)
                raise ValueError(f"Unable to parse statement {pdf_name}: malformed content")

            self.readers[name] = reader

        def set_reader_error(self, name: str, error: Exception):
            def reader(pdf_name, _error=error):
                self.read_calls.append(pdf_name)
                raise _error

            self.readers[name] = reader

        def stub_reader(self, pdf_name: str):
            # Mirrors the production seam's missing-file contract.
            if pdf_name not in self.readers:
                raise FileNotFoundError(
                    f"File {pdf_name} was not found in the folder."
                )
            return self.readers[pdf_name](pdf_name)

        def new_scraper(self, opened_statement: str = _SEED_STATEMENT, pdf_file=None):
            _STATE["most_recent_statement"] = opened_statement
            return PDFScraper(_pdf_file=pdf_file or {1: "seed content"})

        def teardown(self):
            PDFScraper_module.pdf_text_reader = self.original_seam

    backend = StatementBackend()
    try:
        yield backend
    finally:
        backend.teardown()
