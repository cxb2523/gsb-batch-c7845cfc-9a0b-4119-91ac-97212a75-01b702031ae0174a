"""Chart pagination: partial page failures, mirror pinning and completeness reporting.

All responses are produced locally through httpx.MockTransport, so the tests never touch
the network. Tests are written as sync functions calling ``asyncio.run`` so the suite does
not need pytest-asyncio (CI only installs pytest).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from hitmoz.client import HitMoz

BASE = "https://m1.test"


def page_html(prefix: str, n: int, pagination: int | None = None) -> str:
    """A HitMoz-like chart page with ``n`` tracks whose ids start with ``prefix``."""
    items = []
    for i in range(n):
        sid = f"{prefix}{i}"
        meta = {
            "artist": f"Artist {sid}",
            "title": f"Title {sid}",
            "url": f"https://cdn.test/get/cuts/{sid}.mp3",
            "img": f"https://cdn.test/cover/{sid}.jpg",
            "id": f"track-id-{sid}",
        }
        items.append(
            f'<li class="tracks__item track" data-musmeta=\'{json.dumps(meta)}\'>'
            f'<div class="track__fulltime">03:00</div>'
            f'<a href="/get/music/20260101/{sid}.mp3" class="track__download-btn"></a>'
            f"</li>"
        )
    tail = f'<a class="pagination__link" href="/songs/top-today/start/{pagination}"></a>' if pagination else ""
    return "<html><body><ul>" + "".join(items) + "</ul>" + tail + "</body></html>"


def make_client(handler, mirrors: tuple[str, ...] = (BASE,)) -> HitMoz:
    hm = HitMoz(mirrors=list(mirrors), cache_ttl=600)
    hm._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return hm


def run_top_with(handler, limit: int = 100, mirrors: tuple[str, ...] = (BASE,)):
    """Run ``top`` against a mock transport."""

    async def go():
        hm = make_client(handler, mirrors)
        try:
            return await hm.top(limit)
        finally:
            await hm.close()

    return asyncio.run(go())


def chart_handler(failing: set[str] | None = None, redirect_first: bool = False):
    """Serve a 3-page chart. ``failing`` holds paths that should answer 500."""
    failing = failing or set()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(f"{request.url.scheme}://{request.url.host}{path}")
        if redirect_first and path == "/songs/top-today" and request.url.host == "m1.test":
            return httpx.Response(302, headers={"Location": f"https://m2.test{path}"})
        if path in failing and request.url.host != "m2.test":
            return httpx.Response(500, text="boom")
        page = {
            "/songs/top-today": page_html("a", 48),
            "/songs/top-today/start/48": page_html("b", 48),
            "/songs/top-today/start/96": page_html("c", 48),
        }.get(path)
        if page is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=page)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


# ------------------------------------------------------------------ 1. all pages succeed


def test_all_pages_complete_and_deduped():
    handler = chart_handler()
    res = run_top_with(handler, limit=100)

    assert res.pages_total == 3 and res.pages_ok == 3
    assert res.complete is True and res.partial is False
    assert res.count == 100
    assert [s.rank for s in res.songs] == list(range(1, 101))
    assert len({s.id for s in res.songs}) == 100
    # three pages were requested again: first discovery + parallel rest
    assert handler.seen.count(f"{BASE}/songs/top-today") == 1
    assert any(p.endswith("/start/48") for p in handler.seen)
    assert any(p.endswith("/start/96") for p in handler.seen)


def test_all_pages_use_single_pinned_domain():
    res = run_top_with(chart_handler(), limit=100)

    assert res.source.startswith(BASE)
    assert all(s.download.startswith(BASE + "/get/music/") for s in res.songs)
    assert all(s.link.startswith(BASE + "/song/") for s in res.songs)
    assert len({s.download.split("/")[2] for s in res.songs}) == 1


def test_dedup_keeps_order_and_contiguity():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/songs/top-today":
            return httpx.Response(200, text=page_html("a", 48))
        # page 2 repeats the first 10 tracks of page 1, then adds new ones
        if path == "/songs/top-today/start/48":
            return httpx.Response(200, text=page_html("a", 48) + page_html("b", 38))
        if path == "/songs/top-today/start/96":
            return httpx.Response(200, text=page_html("c", 48))
        return httpx.Response(404)

    res = run_top_with(handler, limit=100)
    assert res.complete is True
    assert res.count == 100
    assert [s.rank for s in res.songs] == list(range(1, 101))
    assert len({s.id for s in res.songs}) == 100


# ------------------------------------------------------------------ 2. a middle page fails


def test_middle_page_failure_keeps_successful_pages():
    handler = chart_handler(failing={"/songs/top-today/start/48"})
    res = run_top_with(handler, limit=100)

    assert res.pages_total == 3 and res.pages_ok == 2
    assert res.complete is False and res.partial is True
    # both pages that succeeded are merged, deduped and ranked
    assert res.count == 96
    assert [s.rank for s in res.songs] == list(range(1, 97))
    assert res.songs[0].id == "a0"
    assert res.songs[48].id == "c0"
    assert res.source.startswith(BASE)
    # no replaced page content leaked in
    assert not any(s.id.startswith("b") for s in res.songs)


def test_last_page_failure_still_returns_partial():
    res = run_top_with(chart_handler(failing={"/songs/top-today/start/96"}), limit=100)
    assert res.complete is False
    assert res.pages_ok == 2 and res.pages_total == 3
    assert res.count == 96


def test_middle_page_failure_with_single_page_request_is_complete():
    # limit 40 -> only the first page is needed, so a failing page 2 is irrelevant
    res = run_top_with(chart_handler(failing={"/songs/top-today/start/48"}), limit=40)
    assert res.complete is True
    assert res.pages_ok == res.pages_total == 1
    assert res.count == 40


# ------------------------------------------------------------------ 3. the first page fails


def test_first_page_failure_raises_connection_error():
    with pytest.raises(ConnectionError):
        run_top_with(chart_handler(failing={"/songs/top-today"}), limit=100)


def test_first_page_failure_raises_even_after_mirror_resolution():
    async def go():
        hm = make_client(chart_handler(failing={"/songs/top-today"}))
        hm._resolved = True  # pretend discovery already happened
        try:
            await hm.top(100)
        finally:
            await hm.close()

    with pytest.raises(ConnectionError):
        asyncio.run(go())


# ------------------------------------------------------------------ 4. mirror switch / redirect


def test_redirect_pins_final_domain_everywhere():
    handler = chart_handler(redirect_first=True)
    res = run_top_with(handler, limit=100)

    assert res.complete is True
    assert res.source.startswith("https://m2.test")
    assert all(s.download.startswith("https://m2.test/get/music/") for s in res.songs)
    assert all(s.link.startswith("https://m2.test/song/") for s in res.songs)
    assert not any("m1.test" in s.download or "m1.test" in s.link for s in res.songs)
    # the other pages were requested from the redirect target, not the old mirror
    assert all(p.startswith("https://m2.test/") for p in handler.seen[1:])


def test_mid_batch_mirror_failover_keeps_one_domain():
    """Page 2 dies on m1 and is retried on m2; links and source still use the pinned domain."""

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if path == "/songs/top-today":
            return httpx.Response(200, text=page_html("a", 48))
        if path == "/songs/top-today/start/48":
            if host == "m1.test":
                return httpx.Response(503, text="boom")
            return httpx.Response(200, text=page_html("b", 48))
        if path == "/songs/top-today/start/96":
            return httpx.Response(200, text=page_html("c", 48))
        return httpx.Response(404)

    res = run_top_with(handler, limit=100, mirrors=(BASE, "https://m2.test"))
    assert res.complete is True
    assert res.pages_ok == res.pages_total == 3
    assert res.count == 100
    # every page rendered against the mirror that served page 1
    assert res.source.startswith(BASE)
    assert {s.download.split("/")[2] for s in res.songs} == {"m1.test"}
    assert {s.link.split("/")[2] for s in res.songs} == {"m1.test"}


def test_recovery_after_first_page_failure_on_next_call():
    state = {"fail": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/songs/top-today":
            return httpx.Response(200, text=page_html("b", 10))
        if state["fail"]:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=page_html("a", 48))

    async def go():
        hm = make_client(handler, mirrors=(BASE, "https://m2.test"))
        try:
            with pytest.raises(ConnectionError):
                await hm.top(48)
            state["fail"] = False
            return await hm.top(48)
        finally:
            await hm.close()

    res = asyncio.run(go())
    assert res.complete is True
    assert res.pages_ok == res.pages_total == 1
    assert res.count == 48


# ------------------------------------------------------------------ 5. caching behaviour


def test_partial_result_is_not_cached():
    calls = {"page1": 0, "page2": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/songs/top-today":
            calls["page1"] += 1
            return httpx.Response(200, text=page_html("a", 48))
        if path == "/songs/top-today/start/48":
            calls["page2"] += 1
            # fail first time, succeed later
            if calls["page2"] == 1:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, text=page_html("b", 48))
        if path == "/songs/top-today/start/96":
            return httpx.Response(200, text=page_html("c", 48))
        return httpx.Response(404)

    async def go():
        hm = make_client(handler)
        try:
            first = await hm.top(100)
            cached_keys = [k for k in hm._cache if k.startswith("top:")]
            second = await hm.top(100)
            return first, second, cached_keys
        finally:
            await hm.close()

    first, second, cached_keys = asyncio.run(go())
    assert first.complete is False
    assert cached_keys == []  # nothing partial was stored for reuse
    assert second.complete is True
    assert second.pages_ok == second.pages_total == 3
    assert calls == {"page1": 2, "page2": 2}  # both requests went to the network again


def test_complete_result_is_cached_and_keeps_completeness():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        path = request.url.path
        if path == "/songs/top-today":
            return httpx.Response(200, text=page_html("a", 48))
        if path == "/songs/top-today/start/48":
            return httpx.Response(200, text=page_html("b", 48))
        return httpx.Response(404)

    async def go():
        hm = make_client(handler)
        try:
            first = await hm.top(60)
            second = await hm.top(60)
            return first, second, calls["n"]
        finally:
            await hm.close()

    first, second, requests_made = asyncio.run(go())
    assert first.complete is True and first.pages_ok == first.pages_total == 2
    assert requests_made == 2  # only the first call hit the network
    assert second.cached is True
    assert second.complete is True and second.pages_ok == 2 and second.pages_total == 2


def test_partial_chart_does_not_answer_smaller_request():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        path = request.url.path
        if path == "/songs/top-today":
            return httpx.Response(200, text=page_html("a", 48))
        if path == "/songs/top-today/start/48":
            return httpx.Response(500, text="boom")
        return httpx.Response(404)

    async def go():
        hm = make_client(handler)
        try:
            await hm.top(100)  # partial
            before = calls["n"]
            small = await hm.top(10)  # must not be answered from a partial cache
            return small, calls["n"] - before
        finally:
            await hm.close()

    small, extra_requests = asyncio.run(go())
    assert extra_requests == 1
    assert small.count == 10
    assert small.complete is True
    assert small.pages_ok == small.pages_total == 1


# ------------------------------------------------------------------ 6. serialisation contract


def test_to_dict_keeps_existing_fields_and_adds_completeness():
    complete = run_top_with(chart_handler(), limit=100).to_dict()
    partial = run_top_with(chart_handler(failing={"/songs/top-today/start/48"}), limit=100).to_dict()

    for payload in (complete, partial):
        assert payload["success"] is True
        assert payload["mode"] == "top-today"
        assert isinstance(payload["count"], int) and payload["count"] == len(payload["songs"])
        assert set(payload["songs"][0]) == {
            "rank", "id", "title", "artist", "duration", "seconds", "cover", "download", "stream", "link",
        }
        assert {"source", "cached", "took_ms"} <= set(payload)
        assert {"complete", "pages_ok", "pages_total"} <= set(payload)

    assert complete["complete"] is True
    assert (complete["pages_ok"], complete["pages_total"]) == (3, 3)
    assert partial["complete"] is False
    assert (partial["pages_ok"], partial["pages_total"]) == (2, 3)


# ------------------------------------------------------------------ 7. search still paginates safely


def test_search_partial_pages_do_not_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/search":
            return httpx.Response(200, text=page_html("s", 48, pagination=48))
        if path == "/search/start/48":
            return httpx.Response(500, text="boom")
        return httpx.Response(404)

    async def go():
        hm = make_client(handler)
        try:
            return await hm.search("q", limit=96)
        finally:
            await hm.close()

    res = asyncio.run(go())
    assert res.complete is False
    assert res.pages_ok == 1 and res.pages_total == 2
    assert res.count == 48
    assert all(s.link.startswith(BASE) for s in res.songs)
