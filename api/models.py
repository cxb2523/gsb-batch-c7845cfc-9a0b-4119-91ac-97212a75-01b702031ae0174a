"""Pydantic models defining the public API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SearchRequest(BaseModel):
    """Incoming search query with optional pagination and filters."""

    query: str = Field(..., min_length=1, max_length=512, description="User search query")
    top_k: int = Field(10, ge=1, le=100, description="Number of results to return")
    include_graph: bool = Field(False, description="Attach related knowledge-graph entities")
    filters: dict[str, Any] = Field(default_factory=dict, description="Metadata filters")

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query must not be blank")
        return cleaned


class ResultItem(BaseModel):
    """One ranked hit inside a SearchResponse."""

    doc_id: str
    url: str
    title: str
    snippet: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """Outgoing response envelope for /search."""

    query: str
    results: list[ResultItem]
    total_hits: int
    took_ms: float
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class SuggestionRequest(BaseModel):
    """Prefix for autocomplete suggestions."""

    prefix: str = Field(..., min_length=1, max_length=128)

    @field_validator("prefix")
    @classmethod
    def strip_prefix(cls, value: str) -> str:
        return value.strip()


class SuggestionResponse(BaseModel):
    """Ranked completion candidates for a query prefix."""

    prefix: str
    suggestions: list[str] = Field(max_length=10)


class IndexStatus(BaseModel):
    """Health and size snapshot of the index."""

    documents: int
    chunks: int
    vector_index_ready: bool
    last_updated: datetime | None = None


class ErrorResponse(BaseModel):
    """Uniform error payload."""

    error: str
    detail: str | None = None


class CrawlPageResult(BaseModel):
    """Settlement entry for one URL in a batch crawl."""

    url: str
    outcome: str = Field(..., description="indexed|robots_denied|fetch_failed|duplicate|unprocessed")
    status_code: int | None = None
    chunks: int = 0
    duplicate_of: str | None = None
    error: str | None = None
    depth: int = 0


class CrawlSummary(BaseModel):
    """Aggregate counts for one batch crawl."""

    indexed: int = 0
    robots_denied: int = 0
    fetch_failed: int = 0
    duplicate: int = 0
    unprocessed: int = 0
    total: int = 0
    chunks: int = 0


class CrawlRequest(BaseModel):
    """Batch crawl request: seeds plus optional crawl limits."""

    seeds: list[str] = Field(..., min_length=1)
    max_depth: int | None = Field(None, ge=0, le=10)
    max_pages: int | None = Field(None, ge=1, le=1000)


class CrawlResponse(BaseModel):
    """Per-page settlement plus aggregate counts."""

    pages: list[CrawlPageResult]
    summary: CrawlSummary
