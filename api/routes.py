"""FastAPI routes exposing search, suggestions, indexing, and status endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.models import (
    CrawlPageResult,
    CrawlRequest,
    CrawlResponse,
    CrawlSummary,
    ErrorResponse,
    IndexStatus,
    SearchRequest,
    SuggestionRequest,
    SuggestionResponse,
)
from core.engine import SearchEngine, SearchResponse
from retriever.indexer import Document, IndexManager
from retriever.web import WebCrawler

logger = logging.getLogger(__name__)
router = APIRouter()

_engine: SearchEngine | None = None


def get_engine() -> SearchEngine:
    """Lazily build and cache the process-wide search engine."""
    global _engine
    if _engine is None:
        _engine = SearchEngine()
    return _engine


def get_index(engine: SearchEngine = Depends(get_engine)) -> IndexManager:
    """Expose the engine's index manager to write endpoints."""
    return engine.index


def get_crawler() -> WebCrawler:
    """Build the default crawler; overridable in tests via dependency override."""
    return WebCrawler()


@router.post(
    "/search",
    response_model=SearchResponse,
    responses={503: {"model": ErrorResponse}},
    tags=["search"],
    summary="Run a ranked search",
)
async def search(payload: SearchRequest, engine: SearchEngine = Depends(get_engine)) -> SearchResponse:
    try:
        return engine.search(payload.query, top_k=payload.top_k)
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.exception("search failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post("/suggest", response_model=SuggestionResponse, tags=["search"])
async def suggest(payload: SuggestionRequest) -> SuggestionResponse:
    """Return autocomplete completions from recent popular queries."""
    corpus = ["deep learning tutorial", "deep search algorithms", "deployment guide",
              "knowledge graph basics", "bm25 ranking", "vector embeddings"]
    lowered = payload.prefix.lower()
    matches = sorted(q for q in corpus if q.startswith(lowered))[:10]
    return SuggestionResponse(prefix=payload.prefix, suggestions=matches)


@router.post("/index", status_code=status.HTTP_202_ACCEPTED, tags=["admin"])
async def add_document(doc: dict, index: IndexManager = Depends(get_index)) -> dict:
    """Ingest one document into the index (fire-and-forget style)."""
    required = {"id", "text"}
    if not required.issubset(doc):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"missing fields: {required - set(doc)}")
    document = Document(id=str(doc["id"]), text=doc["text"],
                        url=doc.get("url"), title=doc.get("title"),
                        metadata=doc.get("metadata", {}))
    index.add([document])
    return {"queued": True, "doc_id": document.id}


@router.post("/crawl", response_model=CrawlResponse, tags=["admin"],
             summary="Batch-crawl seeds and report per-page settlement")
def crawl(payload: CrawlRequest,
          index: IndexManager = Depends(get_index),
          crawler: WebCrawler = Depends(get_crawler)) -> CrawlResponse:
    """Crawl seeds, index each fetched page, and return a reconcilable report.

    Every seed/link ends in exactly one outcome, so operators can verify what
    landed in the index, what robots.txt blocked, what failed, what was skipped
    as duplicate content, and what was left unvisited by the page budget.
    """
    if payload.max_depth is not None:
        crawler.max_depth = payload.max_depth
    if payload.max_pages is not None:
        crawler.max_pages = payload.max_pages
    report = crawler.crawl_and_index(payload.seeds, index)
    summary = report.summary
    return CrawlResponse(
        pages=[CrawlPageResult(**page.to_dict()) for page in report.pages],
        summary=CrawlSummary(chunks=report.total_chunks, **summary),
    )


@router.get("/status", response_model=IndexStatus, tags=["admin"])
async def status_endpoint(index: IndexManager = Depends(get_index)) -> IndexStatus:
    """Report index health for load balancers and dashboards."""
    stats = index.stats()
    return IndexStatus(documents=stats["documents"], chunks=stats["chunks"],
                       vector_index_ready=bool(stats["vector_ready"]),
                       last_updated=stats.get("last_updated"))
