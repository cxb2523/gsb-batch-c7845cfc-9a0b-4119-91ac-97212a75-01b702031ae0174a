"""Command line entry point for the crawler.

The crawling rules live in :mod:`crawler` so the CLI and the Streamlit page
behave identically:

* start from ``config.START_URL``;
* follow only same-domain links;
* visit at most ``max_pages`` (200) pages;
* skip ``.pdf``/``.jpg``/``.png``/``.zip``/``.svg`` and similar assets;
* every request times out after 10 seconds;
* a failing page is recorded and the crawl continues.

For the resumable, UI-driven variant see ``crawler.CrawlSession``.  This
module keeps the original ``crawl_website`` function and the
``python scrape.py`` behaviour of writing ``SCRAPED_DATA_PATH``.
"""

import sys

from config import START_URL, SCRAPED_DATA_PATH
from crawler import CrawlSession, MAX_PAGES, save_scraped_data


def crawl_website(start_url, max_pages=MAX_PAGES):
    """Crawl ``start_url`` and return the successful pages as dicts.

    The returned list uses the JSON format consumed by ``ingest.py``:
    ``{"url": ..., "title": ..., "content": ...}``.
    """

    session = CrawlSession(start_url, max_pages=max_pages)
    print(f"--- Starting Crawl on {session.domain} ---")

    success_seen = 0
    failure_seen = 0
    while session.step():
        url = session.current_url
        print(f"\n---> Visiting: {url}")

        if len(session.successes) > success_seen:
            page = session.successes[-1]
            success_seen = len(session.successes)
            print(f"  -> Scraped '{page.title}' ({len(page.content)} chars)")
        elif len(session.failures) > failure_seen:
            failure = session.failures[-1]
            failure_seen = len(session.failures)
            print(f"  -> Failed to fetch {failure.url}: {failure.error}")

    if session.start_url_error:
        print(f"\nSTART_URL is unreachable: {session.start_url_error}")

    return session.to_ingest_records()


if __name__ == "__main__":
    all_content = crawl_website(START_URL)

    if not all_content:
        print(
            "\nNo page could be scraped. "
            f"{SCRAPED_DATA_PATH} was left untouched."
        )
        sys.exit(1)

    save_scraped_data(all_content, SCRAPED_DATA_PATH)

    print(f"\n\nCrawling complete. Scraped {len(all_content)} pages.")
    print(f"Full site content saved to {SCRAPED_DATA_PATH}")
