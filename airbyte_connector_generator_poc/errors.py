"""Pipeline level errors shared by the scraper, preprocessor and CLI.

These live in their own module so they can be imported without pulling in the
OpenAI/Playwright dependencies (which require credentials to import).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from airbyte_connector_generator_poc.report import PageReport


class SkyffelError(Exception):
    """Base class for errors that should be shown to the user, not traced."""


class ScrapeBatchFailedError(SkyffelError):
    """No page in the batch could be scraped."""

    def __init__(self, report: "PageReport"):
        self.report = report
        failed = report.failed_pages
        details = "\n".join(
            f"  - {outcome.url}: {outcome.error}" for outcome in failed)
        super().__init__(
            f"All {len(failed)} documentation URL(s) failed to scrape. "
            "No OpenAPI spec was generated.\n" + details)


class NoUsablePagesError(SkyffelError):
    """Every scraped page also failed during preprocessing."""

    def __init__(self, report: "PageReport"):
        self.report = report
        failed = report.failed_pages
        details = "\n".join(
            f"  - {outcome.url}: {outcome.error}" for outcome in failed)
        super().__init__(
            "No documentation page could be preprocessed. "
            "No OpenAPI spec was generated.\n" + details)
