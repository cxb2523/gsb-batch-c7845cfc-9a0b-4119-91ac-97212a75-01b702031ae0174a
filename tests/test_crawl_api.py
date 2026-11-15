"""API contract for /crawl: per-page settlement is exposed to operators."""

from __future__ import annotations

import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import get_crawler, get_index, router
from retriever.indexer import IndexManager
from retriever.web import WebCrawler

from conftest import FakeRobots, FakeSite


def html(body: str) -> str:
    return f"<html><body><main><p>{body}</p></main></body></html>"


def build_client(site: FakeSite, robots: FakeRobots) -> tuple[TestClient, IndexManager]:
    app = FastAPI()
    app.include_router(router)
    index = IndexManager(chunk_size=128)
    crawler = WebCrawler(fetcher=site.fetch, robots=robots, delay_ms=0)
    app.dependency_overrides[get_index] = lambda: index
    app.dependency_overrides[get_crawler] = lambda: crawler
    return TestClient(app), index


def test_crawl_endpoint_returns_reconcilable_settlement():
    robots = FakeRobots(denied={"https://site.test/denied"})
    site = FakeSite(
        {
            "https://site.test/a": html("Alpha content about vector search."),
            "https://site.test/copy": html("Alpha content about vector search."),
            "https://site.test/b": html("Beta content about chunk overlap."),
        },
        errors={"https://site.test/down": requests.HTTPError(
            "502", response=_response(502))},
    )
    client, index = build_client(site, robots)

    response = client.post("/crawl", json={
        "seeds": [
            "https://site.test/a", "https://site.test/down",
            "https://site.test/copy", "https://site.test/b",
            "https://site.test/denied",
        ],
        "max_pages": 10,
    })

    assert response.status_code == 200
    body = response.json()
    by_url = {page["url"]: page for page in body["pages"]}
    assert by_url["https://site.test/a"]["outcome"] == "indexed"
    assert by_url["https://site.test/down"]["outcome"] == "fetch_failed"
    assert by_url["https://site.test/down"]["status_code"] == 502
    assert by_url["https://site.test/copy"]["outcome"] == "duplicate"
    assert by_url["https://site.test/copy"]["duplicate_of"] == "https://site.test/a"
    assert by_url["https://site.test/denied"]["outcome"] == "robots_denied"

    summary = body["summary"]
    assert summary["indexed"] == 2
    assert summary["fetch_failed"] == 1
    assert summary["duplicate"] == 1
    assert summary["robots_denied"] == 1
    assert summary["unprocessed"] == 0
    assert summary["total"] == 5
    assert summary["chunks"] == index.stats()["chunks"] > 0
    # Per-page chunk counts must reconcile with the summary total.
    assert sum(page["chunks"] for page in body["pages"]) == summary["chunks"]


def test_crawl_endpoint_honours_max_pages():
    site = FakeSite({
        f"https://site.test/{i}": html(f"Unique page number {i} content.")
        for i in range(1, 5)
    })
    client, _ = build_client(site, FakeRobots())

    response = client.post("/crawl", json={
        "seeds": [f"https://site.test/{i}" for i in range(1, 5)],
        "max_pages": 2,
    })

    summary = response.json()["summary"]
    assert summary["indexed"] == 2
    assert summary["unprocessed"] == 2
    assert summary["total"] == 4


def _response(status_code: int) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.url = "https://site.test/"
    return response
