"""
User interface for the Crawler class

Author: Mark Boon
Date: 28/08/2017
Version: 3.0.0
"""

try:  # package usage: `from Web_Crawler.controller import crawl`
    from .crawler import (
        CrawlResult,
        CrawlSession,
        CrawlSummary,
        Worker,
        crawl,
        normalize_url,
    )
except ImportError:  # legacy usage: running controller.py from inside the package dir
    from crawler import (
        CrawlResult,
        CrawlSession,
        CrawlSummary,
        Worker,
        crawl,
        normalize_url,
    )

__all__ = [
    "crawl",
    "Worker",
    "CrawlSession",
    "CrawlResult",
    "CrawlSummary",
    "normalize_url",
]


if __name__ == "__main__":
    # No new CLI sub-command: keep the historical interactive style,
    # e.g. crawl('https://example.com/')
    pass
