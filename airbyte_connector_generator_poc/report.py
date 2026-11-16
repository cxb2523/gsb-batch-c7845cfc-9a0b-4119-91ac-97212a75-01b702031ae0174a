"""Per-page success/failure reporting for the scrape + preprocess pipeline.

The scraper and the preprocessing step both operate on a batch of URLs. A single
unreachable page must not discard the whole batch, so every page gets an outcome
here. The report is printed to the console and persisted in ``.skyffel`` so users
can tell exactly which pages fed the OpenAPI generation.
"""

import json
import os
from dataclasses import dataclass
from typing import Optional

from rich.console import Console
from rich.table import Table

from airbyte_connector_generator_poc.logger import logger
from airbyte_connector_generator_poc.utils import get_skyffel_dir

REPORT_JSON_FILE_NAME = "scrape_report.json"
REPORT_MARKDOWN_FILE_NAME = "scrape_report.md"

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"
STATUS_SKIPPED = "skipped"


@dataclass
class PageOutcome:
    """Lifecycle of a single documentation URL."""

    url: str
    scrape_status: Optional[str] = None
    scrape_error: Optional[str] = None
    preprocessing_status: Optional[str] = None
    preprocessing_error: Optional[str] = None
    cache_hit: Optional[bool] = None

    @property
    def status(self) -> str:
        if self.scrape_status == STATUS_FAILED:
            return STATUS_FAILED
        if self.preprocessing_status == STATUS_FAILED:
            return STATUS_FAILED
        if self.preprocessing_status == STATUS_SUCCESS:
            return STATUS_SUCCESS
        if self.scrape_status == STATUS_SUCCESS:
            # Scraped and waiting for (or aborted before) preprocessing.
            return STATUS_PENDING
        return STATUS_SKIPPED

    @property
    def error(self) -> Optional[str]:
        return self.scrape_error or self.preprocessing_error

    @property
    def failed_stage(self) -> Optional[str]:
        if self.scrape_status == STATUS_FAILED:
            return "scrape"
        if self.preprocessing_status == STATUS_FAILED:
            return "preprocess"
        return None

    @property
    def used_for_generation(self) -> bool:
        # Preprocessing is what actually contributes content to the OpenAPI
        # prompt; only scraped pages ever get there, so this is the definitive
        # signal that a page participated.
        return (
            self.preprocessing_status == STATUS_SUCCESS
            and self.scrape_status != STATUS_FAILED
        )

    @property
    def detail(self) -> str:
        if self.status == STATUS_SUCCESS:
            return "cache hit" if self.cache_hit else "selectors generated"
        if self.status == STATUS_FAILED:
            return self.error or ""
        if self.status == STATUS_PENDING:
            return "scraped, not preprocessed"
        return "not processed"

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "status": self.status,
            "used_for_generation": self.used_for_generation,
            "failed_stage": self.failed_stage,
            "error": self.error,
            "scrape_status": self.scrape_status,
            "preprocessing_status": self.preprocessing_status,
            "cache_hit": self.cache_hit,
        }


class PageReport:
    """Ordered collection of :class:`PageOutcome`, keyed by URL."""

    def __init__(self, urls: Optional[list[str]] = None):
        self._outcomes: dict[str, PageOutcome] = {}
        for url in urls or []:
            self._ensure(url)

    def _ensure(self, url: str) -> PageOutcome:
        if url not in self._outcomes:
            self._outcomes[url] = PageOutcome(url=url)
        return self._outcomes[url]

    def get(self, url: str) -> PageOutcome:
        return self._ensure(url)

    def record_scrape_success(self, url: str) -> None:
        outcome = self._ensure(url)
        outcome.scrape_status = STATUS_SUCCESS
        outcome.scrape_error = None

    def record_scrape_failure(self, url: str, reason: str) -> None:
        outcome = self._ensure(url)
        outcome.scrape_status = STATUS_FAILED
        outcome.scrape_error = reason
        # A page that was not scraped can never be preprocessed.
        if outcome.preprocessing_status is None:
            outcome.preprocessing_status = STATUS_SKIPPED

    def record_preprocessing_success(self, url: str, cache_hit: bool = False) -> None:
        outcome = self._ensure(url)
        outcome.preprocessing_status = STATUS_SUCCESS
        outcome.preprocessing_error = None
        outcome.cache_hit = cache_hit

    def record_preprocessing_failure(self, url: str, reason: str) -> None:
        outcome = self._ensure(url)
        outcome.preprocessing_status = STATUS_FAILED
        outcome.preprocessing_error = reason

    @property
    def outcomes(self) -> list[PageOutcome]:
        return list(self._outcomes.values())

    @property
    def successful_pages(self) -> list[PageOutcome]:
        return [o for o in self._outcomes.values() if o.used_for_generation]

    @property
    def failed_pages(self) -> list[PageOutcome]:
        return [o for o in self._outcomes.values() if o.status == STATUS_FAILED]

    def summary(self) -> dict:
        outcomes = self.outcomes
        return {
            "total": len(outcomes),
            "succeeded": len(self.successful_pages),
            "failed": len(self.failed_pages),
            "pending": sum(1 for o in outcomes if o.status == STATUS_PENDING),
            "skipped": sum(1 for o in outcomes if o.status == STATUS_SKIPPED),
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "pages": [o.to_dict() for o in self.outcomes],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Page report",
            "",
            f"- Total: {len(self.outcomes)}",
            f"- Used for OpenAPI generation: {len(self.successful_pages)}",
            f"- Failed: {len(self.failed_pages)}",
            "",
            "| URL | Status | Detail |",
            "| --- | --- | --- |",
        ]
        for outcome in self.outcomes:
            lines.append(
                f"| {outcome.url} | {outcome.status} | {_escape_table_cell(outcome.detail)} |"
            )
        return "\n".join(lines) + "\n"

    def write(self, directory: Optional[str] = None) -> tuple[str, str]:
        """Persist the report to ``.skyffel``.

        Returns the paths of the JSON and Markdown reports.
        """
        base_dir = directory or get_skyffel_dir()
        os.makedirs(base_dir, exist_ok=True)

        json_path = os.path.join(base_dir, REPORT_JSON_FILE_NAME)
        markdown_path = os.path.join(base_dir, REPORT_MARKDOWN_FILE_NAME)

        with open(json_path, "w", encoding="utf-8") as json_file:
            json.dump(self.to_dict(), json_file, indent=2)
        with open(markdown_path, "w", encoding="utf-8") as markdown_file:
            markdown_file.write(self.to_markdown())

        logger.debug("Page report written: %s", json_path)
        return json_path, markdown_path

    def print_summary(self, console: Optional[Console] = None) -> None:
        console = console or Console(log_time=False)
        table = Table(
            title="Documentation pages", show_lines=False, expand=True)
        table.add_column("URL", overflow="fold")
        table.add_column("Status", no_wrap=True)
        table.add_column("Used for generation", no_wrap=True)
        table.add_column("Detail", overflow="fold")

        for outcome in self.outcomes:
            if outcome.status == STATUS_SUCCESS:
                status_cell = "[green]scraped[/green]"
                used_cell = "[green]yes[/green]"
                detail = outcome.detail
            elif outcome.status == STATUS_FAILED:
                stage = "scrape" if outcome.failed_stage == "scrape" else "preprocess"
                status_cell = f"[red]{stage} failed[/red]"
                used_cell = "[red]no[/red]"
                detail = outcome.error or ""
            elif outcome.status == STATUS_PENDING:
                status_cell = "[yellow]not preprocessed[/yellow]"
                used_cell = "[yellow]no[/yellow]"
                detail = outcome.detail
            else:
                status_cell = "[yellow]skipped[/yellow]"
                used_cell = "[yellow]no[/yellow]"
                detail = outcome.detail

            table.add_row(outcome.url, status_cell, used_cell, detail)

        console.print(table)

        summary = self.summary()
        console.log(
            f"[bold]{summary['succeeded']}/{summary['total']}[/bold] page(s) used for OpenAPI generation"
            + (f", [red]{summary['failed']} failed[/red]" if summary["failed"] else "")
        )
        for outcome in self.failed_pages:
            console.log(f"[red]FAILED {outcome.url}[/red]: {outcome.error}")


def _escape_table_cell(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")
