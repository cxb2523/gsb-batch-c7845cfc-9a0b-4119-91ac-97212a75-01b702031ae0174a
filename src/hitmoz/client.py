"""Async HitMoz client.

* parallel page fetching over one keep-alive HTTP/2 connection
* parsing with precompiled regexes (no DOM, ~1 ms per page)
* automatic mirror discovery + failover (eu.hitmoz.com, ru.hitmox.com, ...)
* in-memory TTL cache with request coalescing
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote_plus

import httpx

PAGE_SIZE = 48
DEFAULT_MIRRORS = ("https://eu.hitmoz.com", "https://ru.hitmox.com", "https://hitmoz.com", "https://hitmo.me")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

_ITEM = re.compile(r'<li class="tracks__item[^"]*"[^>]*?data-musmeta=\'([^\']+)\'(.*?)</li>', re.S)
_TIME = re.compile(r'track__fulltime">\s*([\d:]+)')
_DL = re.compile(r'href="(/get/music/[^"]+\.mp3)"')
_LAST_PAGE = re.compile(r'/start/(\d+)')


@dataclass(slots=True)
class Song:
    rank: int
    id: str
    title: str
    artist: str
    duration: str
    seconds: int
    cover: str
    download: str
    stream: str
    link: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Result:
    mode: str
    source: str
    songs: list[Song] = field(default_factory=list)
    query: str | None = None
    cached: bool = False
    took_ms: int = 0
    # Completeness of a paginated fetch: every requested page answered?
    complete: bool = True
    # pages that answered vs. pages requested (`pages_total == 0` = not paginated)
    pages_ok: int = 0
    pages_total: int = 0

    def __post_init__(self) -> None:
        # Keep the marker honest whenever the caller supplied page counters.
        if self.pages_total:
            self.complete = self.pages_ok >= self.pages_total

    @property
    def count(self) -> int:
        return len(self.songs)

    @property
    def partial(self) -> bool:
        """True when some requested page failed and the result is incomplete."""
        return not self.complete

    def _clone(
        self,
        *,
        songs: list[Song] | None = None,
        cached: bool | None = None,
        took_ms: int | None = None,
    ) -> Result:
        """Copy with the same completeness info (used when slicing or serving from cache)."""
        return Result(
            self.mode,
            self.source,
            self.songs if songs is None else songs,
            self.query,
            cached=self.cached if cached is None else cached,
            took_ms=self.took_ms if took_ms is None else took_ms,
            complete=self.complete,
            pages_ok=self.pages_ok,
            pages_total=self.pages_total,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"success": True, "mode": self.mode}
        if self.query is not None:
            d["query"] = self.query
        d.update(
            source=self.source,
            count=self.count,
            cached=self.cached,
            took_ms=self.took_ms,
            complete=self.complete,
            pages_ok=self.pages_ok,
            pages_total=self.pages_total,
            songs=[s.to_dict() for s in self.songs],
        )
        return d


@dataclass(slots=True)
class _Pages:
    """Outcome of one multi-page batch: successful HTML pages in request order + the pinned mirror."""

    texts: list[str]
    base: str
    ok: int
    total: int


def _secs(t: str) -> int:
    try:
        parts = [int(x) for x in t.split(":")]
    except ValueError:
        return 0
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def parse_page(page: str, base: str) -> list[dict[str, Any]]:
    """Extract tracks from one HTML page. Pure function, very fast."""
    out = []
    for meta_raw, body in _ITEM.findall(page):
        try:
            meta = json.loads(html.unescape(meta_raw))
        except ValueError:
            continue
        sid = str(meta.get("id", "")).replace("track-id-", "")
        dl = _DL.search(body)
        tm = _TIME.search(body)
        duration = tm.group(1) if tm else ""
        out.append(
            {
                "id": sid,
                "title": (meta.get("title") or "").strip(),
                "artist": (meta.get("artist") or "").strip(),
                "duration": duration,
                "seconds": _secs(duration),
                "cover": meta.get("img") or "",
                "download": base + dl.group(1) if dl else meta.get("url", ""),
                "stream": meta.get("url") or "",
                "link": f"{base}/song/{sid}" if sid else "",
            }
        )
    return out


class HitMoz:
    """Async client. Use as `async with HitMoz() as hm:` or keep one instance for the app lifetime."""

    def __init__(
        self,
        mirrors: tuple[str, ...] | list[str] | None = None,
        proxy: str | None = None,
        timeout: float = 12.0,
        cache_ttl: int = 600,
        concurrency: int = 8,
    ) -> None:
        env = os.getenv("HITMOZ_MIRRORS")
        self.mirrors = list(mirrors or (env.split(",") if env else DEFAULT_MIRRORS))
        self.base = self.mirrors[0].rstrip("/")
        self._resolved = False
        self.cache_ttl = int(os.getenv("HITMOZ_CACHE_TTL", cache_ttl))
        self._sem = asyncio.Semaphore(concurrency)
        self._cache: dict[str, tuple[float, Result]] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        try:
            import h2  # noqa: F401

            http2 = True
        except ImportError:
            http2 = False
        self._http = httpx.AsyncClient(
            headers=HEADERS,
            http2=http2,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=5.0),
            proxy=proxy or os.getenv("HITMOZ_PROXY") or None,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=60),
        )

    async def __aenter__(self) -> "HitMoz":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ http

    async def _fetch(self, path: str, base: str | None = None) -> tuple[str, str]:
        """GET with mirror failover. Returns ``(html, base_that_answered)``.

        Deliberately does not write ``self.base``: a batch of concurrent pages must all be
        rendered against the same mirror, otherwise each response could move the base under
        the others and download/link/source URLs would mix domains.
        """
        primary = (base or self.base).rstrip("/")
        order = [primary] + [m.rstrip("/") for m in self.mirrors if m.rstrip("/") != primary]
        last: Exception | None = None
        for candidate in order:
            try:
                async with self._sem:
                    r = await self._http.get(candidate + path)
                if r.status_code == 200 and "<html" in r.text[:3000].lower():
                    return r.text, f"{r.url.scheme}://{r.url.host}"
                last = httpx.HTTPStatusError(f"{r.status_code} from {candidate}", request=r.request, response=r)
            except httpx.HTTPError as e:
                last = e
        raise ConnectionError(f"All mirrors failed: {last}")

    async def _get(self, path: str) -> str:
        """GET with mirror failover. Remembers the mirror that answered (after redirects)."""
        try:
            text, base = await self._fetch(path)
        except ConnectionError:
            self._resolved = False  # rediscover the live mirror on the next request
            raise
        self.base, self._resolved = base, True
        return text

    async def _pages(self, paths: list[str]) -> _Pages:
        """Fetch pages concurrently, tolerating failures of everything but the first page.

        The first page also discovers the live mirror; its domain is pinned as the base for
        the whole batch, so every successfully fetched page is rendered against one domain.
        """
        total = len(paths)
        if not self._resolved:  # discover the live mirror first, then everything goes in parallel
            try:
                first, base = await self._fetch(paths[0])
            except ConnectionError:
                self._resolved = False  # rediscover the live mirror on the next request
                raise
            self.base, self._resolved = base, True
            rest = await asyncio.gather(*(self._fetch(p, base) for p in paths[1:]), return_exceptions=True)
            ok = [x for x in rest if isinstance(x, tuple)]
            return _Pages([first, *(text for text, _ in ok)], base, 1 + len(ok), total)
        got = await asyncio.gather(*(self._fetch(p, self.base) for p in paths), return_exceptions=True)
        if isinstance(got[0], BaseException):  # the first page is not optional
            self._resolved = False  # rediscover the live mirror on the next request
            raise got[0]
        self.base = got[0][1]  # pin the domain the first page actually landed on
        self._resolved = True
        ok = [x for x in got if isinstance(x, tuple)]
        return _Pages([text for text, _ in ok], self.base, len(ok), total)

    # ------------------------------------------------------------------ cache

    async def _cached(self, key: str, factory) -> Result:
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]._clone(cached=True, took_ms=0)
        if key in self._inflight:  # coalesce identical concurrent requests
            return await asyncio.shield(self._inflight[key])
        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            t = time.perf_counter()
            res = await factory()
            res.took_ms = int((time.perf_counter() - t) * 1000)
            # only a complete result may be reused later; partial charts stay request-scoped
            if res.songs and res.complete and self.cache_ttl > 0:
                self._cache[key] = (time.monotonic() + self.cache_ttl, res)
            fut.set_result(res)
            return res
        except Exception as e:
            fut.set_exception(e)
            fut.exception()  # mark retrieved
            raise
        finally:
            self._inflight.pop(key, None)

    # ------------------------------------------------------------------ public api

    @staticmethod
    def _build(raw_pages: list[list[dict]], limit: int) -> list[Song]:
        seen, songs = set(), []
        for page in raw_pages:
            for s in page:
                key = s["id"] or s["download"]
                if key in seen:
                    continue
                seen.add(key)
                songs.append(Song(rank=len(songs) + 1, **s))
                if len(songs) >= limit:
                    return songs
        return songs

    @staticmethod
    def _result(
        mode: str,
        source: str,
        raw_pages: list[list[dict]],
        limit: int,
        ok: int,
        total: int,
        query: str | None = None,
    ) -> Result:
        return Result(
            mode,
            source,
            HitMoz._build(raw_pages, limit),
            query=query,
            complete=ok == total,
            pages_ok=ok,
            pages_total=total,
        )

    async def top(self, limit: int = 100) -> Result:
        """Today's chart. limit up to ~500."""
        limit = max(1, min(limit, 500))
        pages = -(-limit // PAGE_SIZE)

        async def run() -> Result:
            paths = ["/songs/top-today"] + [f"/songs/top-today/start/{PAGE_SIZE * i}" for i in range(1, pages)]
            got = await self._pages(paths)
            raw = [parse_page(p, got.base) for p in got.texts]
            return self._result(
                "top-today",
                got.base + "/songs/top-today",
                raw,
                pages * PAGE_SIZE,
                got.ok,
                got.total,
            )

        # any fresh chart that is big enough answers smaller requests instantly
        now = time.monotonic()
        for key, (exp, res) in list(self._cache.items()):
            if key.startswith("top:") and exp > now and res.complete and res.count >= limit:
                return res._clone(songs=res.songs[:limit], cached=True, took_ms=0)
        res = await self._cached(f"top:{pages * PAGE_SIZE}", run)
        if res.count > limit:
            res = res._clone(songs=res.songs[:limit])
        return res

    async def search(self, query: str, limit: int = 48) -> Result:
        """Search by artist or title. limit up to ~400."""
        query = query.strip()
        if not query:
            raise ValueError("Empty query")
        limit = max(1, min(limit, 400))
        q = quote_plus(query)

        async def run() -> Result:
            first = await self._get(f"/search?q={q}")
            raw = [parse_page(first, self.base)]
            starts = [int(x) for x in _LAST_PAGE.findall(first)]
            last = max(starts, default=0)
            want = min(last // PAGE_SIZE + 1, -(-limit // PAGE_SIZE))
            more = want > 1 and bool(raw[0])  # an empty first page means there is nothing to paginate
            ok, total = 1, want if more else 1
            if more:
                fetched = await asyncio.gather(
                    *(self._fetch(f"/search/start/{PAGE_SIZE * i}?q={q}", self.base) for i in range(1, want)),
                    return_exceptions=True,
                )
                for x in fetched:
                    if isinstance(x, tuple):
                        raw.append(parse_page(x[0], self.base))
                        ok += 1
            return self._result("search", f"{self.base}/search?q={q}", raw, limit, ok, total, query=query)

        return await self._cached(f"search:{query.lower()}:{limit}", run)


# ---------------------------------------------------------------------- sync helpers


def top(limit: int = 100) -> Result:
    """Blocking helper: `hitmoz.client.top()`."""

    async def run():
        async with HitMoz() as hm:
            return await hm.top(limit)

    return asyncio.run(run())


def search(query: str, limit: int = 48) -> Result:
    """Blocking helper: `hitmoz.client.search("Jony")`."""

    async def run():
        async with HitMoz() as hm:
            return await hm.search(query, limit)

    return asyncio.run(run())
