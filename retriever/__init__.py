"""DeepSearch retriever package: crawling, indexing, and chunking utilities."""

from retriever.chunker import Chunker
from retriever.indexer import (
    Document,
    IndexManager,
    IngestResult,
    IngestStatus,
)

__all__ = [
    "Chunker",
    "Document",
    "IndexManager",
    "IngestResult",
    "IngestStatus",
    "CrawlReport",
    "CrawledPage",
    "PageOutcome",
    "PageReport",
    "WebCrawler",
    "crawl_and_index",
]

# Crawling utilities live in ``retriever.web``, which depends on ``requests``.
# Resolve them lazily so ``import retriever`` keeps working in environments
# that only need the indexing path.
_LAZY_WEB_EXPORTS = {
    "CrawlReport",
    "CrawledPage",
    "PageOutcome",
    "PageReport",
    "WebCrawler",
    "crawl_and_index",
}


def __getattr__(name: str):
    if name in _LAZY_WEB_EXPORTS:
        from retriever import web

        return getattr(web, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
