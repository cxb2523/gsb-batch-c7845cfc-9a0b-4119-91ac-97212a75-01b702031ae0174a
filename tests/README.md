# Monthly Read-Back Tests — Substitute Verification Scope

The repository contains no real Schwab statements and no
`PythonScripts/ScrapingScripts/config.json`, so the production package cannot be imported
or run end to end as-is. The tests therefore use two interchangeable substitutes. Neither
ships any customer data; all fixtures are generated locally in temporary directories.

## How the substitutes attach

`PDFScraper.swap_statement` reads through a module-level seam:

```python
pdf_text_reader: Callable[[str], Dict[int, str]] = _read_pdf
```

The default is the production `_read_pdf` (fitz/PyMuPDF). Tests replace this callable or
point it back at the real reader over generated files. `FileManagement`, which would
otherwise fail importing without `config.json`, is replaced in `conftest.py` with a small
stub exposing only the attributes `PDFScraper`/`PDFTextAnalyst` reference.

## 1. In-memory reader stub — `test_monthly_iterator_stub.py`

No PDF bytes and no fitz involvement. Each month's outcome is injected directly:

- **normal** — `backend.set_statement(name, pages)` returns canned page text;
- **missing (缺页/缺期)** — the month is left unregistered and the stub raises
  `FileNotFoundError`, matching `_read_pdf`'s missing-file contract;
- **parse failure (解析失败)** — `backend.set_corrupt` / `backend.set_reader_error` make
  the reader raise a non-`FileNotFoundError` exception; extraction-function failures
  (e.g. a section missing from the parsed pages) are covered the same way.

Coverage: happy-path alignment for both iterators, mixed success/missing/parse-failure
isolation, extraction errors classified as parse errors, all-months-fail returning an
empty frame without raising, per-period ordering and status enum values, restoration to
the originally opened statement, and restoration failures being reported instead of
raised.

## 2. Real local PDFs — `test_real_pdf_pipeline.py`

PyMuPDF generates genuine one-page PDFs named `YYYY-Month.pdf` inside a temporary
statement directory; the production `_read_pdf`/fitz code path is exercised unchanged:

- **normal** — valid PDFs whose extracted text is asserted inside the extraction
  function;
- **missing** — the generated file is deleted before the run;
- **parse failure** — the file is overwritten with bytes fitz cannot open.

This covers the actual file-open and text-extraction boundary that the in-memory stub
deliberately bypasses, including fitz's own exception being classified as a parse error.

## What is NOT covered

- The full Schwab text-analysis stack (`PDFTextAnalyst`, `TextExtraction`,
  `TextProcessing`) against authentic multi-page statements: representative section
  layouts, headers and numeric formats are not reproduced. Extraction failures are
  simulated by raising from the injected extraction function instead.
- `FinancialAnalyst.decorator_monthly_iteration` / `decorator_custom_iteration` are
  separate parallel implementations and were outside the requested change scope; only
  `PDFScraper.monthly_iterator` and `monthly_iterator_multi_column_frame` were modified.
- Downstream portfolio calculations (`PortfolioRisk`, `PortfolioReturns`) consume the
  `FinancialAnalyst` decorators and are not executed here (they additionally require
  market-data and Excel dependencies).

## Running

```bash
python -I -m pytest tests/ -v
```

The suite is written to work on both the project-pinned pandas 1.4.x and newer pandas
3.x (column assignments use one-element lists to avoid the removed scalar-broadcasting
behaviour).
