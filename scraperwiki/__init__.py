"""
Local version of ScraperWiki Utils, documentation here:
https://scraperwiki.com/docs/python/python_help_documentation/
"""

from . import sql, utils
from .errors import (
    CPUTimeExceededError,
    Error,
    ScrapeError,
    ScrapeHTTPError,
    ScrapeNetworkError,
    ScrapeTimeoutError,
)
from .utils import pdftoxml, scrape, status, swimport

# Compatibility
sqlite = sql

__all__ = [
    "CPUTimeExceededError",
    "Error",
    "ScrapeError",
    "ScrapeHTTPError",
    "ScrapeNetworkError",
    "ScrapeTimeoutError",
    "pdftoxml",
    "scrape",
    "sql",
    "status",
    "swimport",
    "utils",
]
