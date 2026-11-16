import traceback
from typing import Optional

import yaml
from dotenv import load_dotenv
import typer
from airbyte_connector_generator_poc.utils import (check_env_for_key, write_debug_file, write_env_variable, split_urls,
                                                   nuke_debug_directory, remove_stale_output_files)
from airbyte_connector_generator_poc.scraper import scrape_urls
from airbyte_connector_generator_poc.report import PageReport
from airbyte_connector_generator_poc.errors import (
    ScrapeBatchFailedError, SkyffelError)
from asyncio import run
from airbyte_connector_generator_poc.logger import logger
from rich.console import Console
from rich.status import Status
from rich.columns import Columns
from rich.panel import Panel

load_dotenv()

console = Console(log_time=False)


def _record_invalid_urls(page_report: PageReport, invalid_urls: list[str]) -> None:
    for url in invalid_urls:
        logger.warning("Failed to scrape %s: %s", url, "invalid URL")
        page_report.record_scrape_failure(url, "invalid URL")


async def build_openapi_spec(goal: str, urls: list[str], report: Optional[PageReport] = None):
    """Scrape + preprocess the documentation pages and generate an OpenAPI spec.

    Individual page failures are tolerated; the batch only aborts when every
    page failed. The per-page report is written to ``.skyffel`` before raising,
    so users still see which pages failed.
    """
    from airbyte_connector_generator_poc.openapi_generator import generate_openapi_spec

    urls = [url.strip() for url in urls]
    console.log("Scraping URLs")
    console.print(Columns([Panel(url, expand=True) for url in urls]))

    page_report = report if report is not None else PageReport(urls)

    # A syntactically invalid URL is just another unusable page: report it and
    # keep going with the rest of the batch.
    valid_urls, invalid_urls = split_urls(urls)
    _record_invalid_urls(page_report, invalid_urls)

    if not valid_urls:
        page_report.write()
        page_report.print_summary(console)
        raise ScrapeBatchFailedError(page_report)

    logger.debug("Start scraping URLs: %s", valid_urls)
    html_documents = await scrape_urls(valid_urls, report=page_report)
    page_report = getattr(html_documents, "report", page_report)
    # If the scraper returned a different report object, re-apply the invalid
    # URL outcomes so the final report still lists every requested URL.
    _record_invalid_urls(page_report, invalid_urls)
    logger.debug("Scraped HTML documents")

    if not html_documents:
        page_report.write()
        page_report.print_summary(console)
        raise ScrapeBatchFailedError(page_report)

    try:
        openapi_spec = await generate_openapi_spec(
            url_html_documents=html_documents,
            user_goal=goal,
            page_report=page_report)
    except Exception:
        # Persist the partial page report even if generation blew up, and tell
        # the user which pages actually contributed.
        page_report.write()
        page_report.print_summary(console)
        raise

    page_report.write()
    page_report.print_summary(console)

    return openapi_spec


async def _main(goal: str, urls: list[str]):
    nuke_debug_directory()
    # A connector left over from a previous run must not be mistaken for the
    # output of this run (which may fail entirely).
    remove_stale_output_files()

    if not goal:
        goal = typer.prompt("Tell us, what do you want to integrate?")
    if not check_env_for_key("OPENAI_API_KEY"):
        typer.echo(
            "We'll need an api key to generate the code. We'll store it in a .env file")
        openai_api_key = typer.prompt(
            "Please enter your OpenAI API key", hide_input=True)
        if not openai_api_key.startswith("sk-"):
            raise ValueError("OpenAI API key is required.")
        write_env_variable("OPENAI_API_KEY", openai_api_key)

    from airbyte_connector_generator_poc.airbyte.airbyte import generate_airbyte_connector, validate_airbyte_connector
    from airbyte_connector_generator_poc.openapi_spec import load_openapi_spec_from_path_or_url

    console.log("[bold]Starting...")

    has_openapi_spec = False

    if has_openapi_spec:
        openapi_spec_path = typer.prompt(
            "Please enter the path or url to the OpenAPI spec file")
        openapi_spec = load_openapi_spec_from_path_or_url(
            openapi_spec_path)
    else:
        openapi_spec = await build_openapi_spec(goal, urls)

    with open("openapi.yaml", 'w') as file:
        yaml.safe_dump(openapi_spec, file)

    with Status("[bold cyan] Generating Airbyte connector...", console=console):
        airbyte_connector = generate_airbyte_connector(openapi_spec)

        console.log("[bold green]  ✅ Airbyte connector generated")

        logger.debug("Writing airbyte connector")
        with open("airbyte_connector.yaml", 'w') as f:
            yaml.safe_dump(airbyte_connector, f)

        ok, err = validate_airbyte_connector(airbyte_connector)

        if ok:
            console.log("[green bold]  ✅ Airbyte connector is valid")
        else:
            console.log(
                "[red bold]  ❌ Airbyte connector is invalid (check .skyffel/airbyte_validation_error.log for more details)")
            write_debug_file("airbyte_validation_error.log",
                             "".join(traceback.format_exception(err)))
            exit(1)

    console.bell()


def run_pipeline(goal: str, urls: list[str]):
    """Run the pipeline, turning expected batch failures into a clean exit."""
    try:
        run(_main(goal, urls))
    except SkyffelError as error:
        console.log(f"[red bold]❌ {error}[/red bold]")
        raise typer.Exit(code=1)


def main(goal: str = typer.Option(), urls: list[str] = typer.Option()):
    run_pipeline(goal, urls)


def cli():
    typer.run(main)


if __name__ == "__main__":
    typer.run(main)
