import os
import traceback
from typing import Optional

from rich.console import Console
from rich.status import Status
from rich.progress import Progress
from rich.progress import SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn

from airbyte_connector_generator_poc.utils import write_debug_file, get_skyffel_dir
from airbyte_connector_generator_poc.docs_parser import (convert_html_to_markdown,
                                                         extract_relevant_html)
from dotenv import load_dotenv
from airbyte_connector_generator_poc.local_cache import LocalCache
from airbyte_connector_generator_poc.openapi_spec import (clean_openapi_spec, generate_openapi_spec_from_markdown,
                                                          validate_openapi_spec)
from airbyte_connector_generator_poc.report import PageReport
from airbyte_connector_generator_poc.errors import NoUsablePagesError

from airbyte_connector_generator_poc.logger import logger

import hashlib

load_dotenv()

console = Console(log_time=False)
progress = Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    TimeElapsedColumn(),
)


def preprocess_page(html_document: str, url_key: str, url_selector_cache: LocalCache):
    """Select the main content of a page, reusing cached selectors when available.

    Returns ``(relevant_html, selectors, cache_hit)``. A cached selector can go
    stale if the page's HTML changed; in that case the cached selectors are
    retried once by re-electing them. The old cache entry is only replaced when
    the re-election succeeds, so a failed page never disturbs the cache.
    """
    cached_selectors = url_selector_cache.get(key=url_key) or {}
    main_section_selector = cached_selectors.get("main_section_selector")
    irrelevant_sections_selectors = cached_selectors.get(
        "irrelevant_sections_selectors")

    cache_hit = (
        main_section_selector is not None
        and irrelevant_sections_selectors is not None
    )

    if cache_hit:
        try:
            relevant_html, main_section_selector, irrelevant_sections_selectors = extract_relevant_html(
                html_document, main_section_selector, irrelevant_sections_selectors)
        except Exception as error:  # noqa: BLE001 - stale cache, re-elect below
            logger.warning(
                "Cached selectors failed for %s (%s); re-electing selectors",
                url_key, " ".join(str(error).split()))
            cache_hit = False
        else:
            return relevant_html, {
                "main_section_selector": main_section_selector,
                "irrelevant_sections_selectors": irrelevant_sections_selectors,
            }, True

    relevant_html, main_section_selector, irrelevant_sections_selectors = extract_relevant_html(
        html_document)

    selectors = {
        "main_section_selector": main_section_selector,
        "irrelevant_sections_selectors": irrelevant_sections_selectors,
    }
    return relevant_html, selectors, cache_hit


async def generate_openapi_spec(url_html_documents: dict, user_goal: str,
                                page_report: Optional[PageReport] = None):
    if not url_html_documents:
        raise NoUsablePagesError(
            page_report or PageReport(list(url_html_documents)))

    logger.debug(f"Start generating OpenAPI spec for user goal: {user_goal}")

    # Only successfully scraped pages reach this function, so merging the
    # mapping values cannot pull in the empty string of a failed page.
    write_debug_file("html_documents_combined.html",
                     "\n".join(url_html_documents.values()))

    url_selector_cache = LocalCache(
        os.path.join(get_skyffel_dir(), 'selector_cache.json'))

    relevant_html_tags = []

    with progress:
        preprocessing_task = progress.add_task(
            "[bold green]Preprocessing docs...", total=len(url_html_documents.keys()))

        for url, html_document in url_html_documents.items():
            progress.update(preprocessing_task,
                            description=f"[bold cyan]Preprocessing[/bold cyan] {url}")

            url_key = hashlib.md5(url.encode()).hexdigest()

            try:
                relevant_html, selectors, cache_hit = preprocess_page(
                    html_document, url_key, url_selector_cache)
            except Exception as error:  # noqa: BLE001 - one bad page must not sink the batch
                reason = " ".join(
                    str(error).split()) or error.__class__.__name__
                logger.warning("Failed to preprocess %s: %s", url, reason)
                if page_report is not None:
                    page_report.record_preprocessing_failure(url, reason)
                progress.update(preprocessing_task, advance=1)
                continue

            write_debug_file(f"relevant_html_{url_key}.html",
                             relevant_html.prettify())

            # Only pages that were scraped *and* preprocessed successfully may
            # write selectors, so a failure can never poison the cache.
            if not cache_hit:
                url_selector_cache.set(key=url_key, value={
                    **selectors,
                    "url": url,
                })

            if page_report is not None:
                page_report.record_preprocessing_success(url, cache_hit=cache_hit)

            relevant_html_tags.append(relevant_html)

            progress.update(preprocessing_task, advance=1)

        progress.update(preprocessing_task,
                        description="[bold green]✅ Preprocessed docs")

    if not relevant_html_tags:
        raise NoUsablePagesError(page_report or PageReport(list(url_html_documents)))

    relevant_html_combined = "\n".join(
        [tag.prettify() for tag in relevant_html_tags])

    write_debug_file("relevant_html_combined.html", relevant_html_combined)

    logger.debug("Start converting relevant HTML to markdown")
    markdown = convert_html_to_markdown(relevant_html_combined)
    write_debug_file("markdown.md", markdown)

    with Status("[bold cyan]Generating OpenAPI spec...", console=console):
        openapi_spec_json = await generate_openapi_spec_from_markdown(markdown, user_goal)
        clean_openapi_spec(openapi_spec_json)
        console.log("[bold green]  ✅ OpenAPI spec generated")

    ok, err = validate_openapi_spec(openapi_spec_json)

    if ok:
        console.log("[green bold]  ✅ OpenAPI spec is valid")
    else:
        console.log(
            "[red bold]  ❌ OpenAPI spec is invalid (check .skyffel/openapi_validation_error.log for more details)")
        write_debug_file("openapi_validation_error.log",
                         "".join(traceback.format_exception(err)))
        exit(1)

    return openapi_spec_json
