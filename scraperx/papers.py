"""Find, verify and read scholarly papers (the classic pre-2010 algorithm papers included) from keyless open indexes.

WHY (operator, 2026-11-01 10:3x GMT): "sa pejpery dla algorytmow, matematyczne itd, prawda? sprzed ery ai? scrfaperx chyba
umie ich szukac, a jak nie to powinien, zebys ty mial latwiej". Until then scraperx had no paper search. An agent designing a
store or an hourly job had no cheap way to find the papers that already solved its problem (materialized view maintenance,
sliding-window aggregation, top-k maintenance, partial indexes), or to check a citation before quoting it.

SOURCES (no key, no account; each one is optional and fails soft into ``errors``, never into "no results"):
  openalex  api.openalex.org/works            about 250 M works; citations, abstract, an open-access PDF link when one exists
  crossref  api.crossref.org/works            every DOI-registered paper (ACM, IEEE, Springer, SIAM ...) with its print year
  arxiv     export.arxiv.org/api/query        preprints with a free PDF (1991 onward; few classic database papers)
  s2        api.semanticscholar.org/graph/v1  Semantic Scholar; keyless calls share one public rate limit, so a 429 is common
  dblp      dblp.org/search/publ/api          NOT in the default set: every dblp host answers scripts with an Anubis
                                              proof-of-work page (HTTP 202, measured 2026-11-01); asked by name, it reports
                                              that wall instead of "no results"
Set SCRAPERX_MAILTO=<address> to join the OpenAlex and Crossref polite pools (optional).

COMMANDS
  scraperx papers search "sliding window aggregation" --before 2010 [-n 10] [--source all|openalex|crossref|arxiv|s2|dblp] [--json]
  scraperx papers cite "No pane, no gain"        best title match with a similarity score (cite-or-retract)
  scraperx papers read 10.1145/1058150.1058158   find an open-access PDF, download it, extract its text (DOI, arXiv id or
                                                 a title); the PDF and a .txt land in ~/.scraperx/papers/ (or --out DIR)

Python:
  from scraperx.papers import search, cite, read
  papers, errors = search("top-k view maintenance", year_max=2006)
"""

from __future__ import annotations

import argparse
import difflib
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from scraperx._safe_xml import safe_fromstring

SOURCES = ("openalex", "crossref", "arxiv", "s2")
ALL_SOURCES = SOURCES + ("dblp",)
TIMEOUT_S = 20
PDF_MAX_BYTES = 60 * 1024 * 1024
_UA = "scraperx-papers/1.0 (+https://github.com/; research tool)"
_ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class Paper:
    title: str
    year: int | None = None
    venue: str | None = None
    authors: list[str] = field(default_factory=list)
    doi: str | None = None
    url: str | None = None
    citations: int | None = None
    oa_pdf: str | None = None
    abstract: str | None = None
    sources: list[str] = field(default_factory=list)

    def line(self) -> str:
        au = ", ".join(self.authors[:4]) + (" et al." if len(self.authors) > 4 else "")
        bits = [f"[{self.year or '?'}] {self.title}", f"  {au}" if au else "", f"  {self.venue}" if self.venue else ""]
        tail = []
        if self.citations is not None:
            tail.append(f"cited {self.citations}")
        if self.doi:
            tail.append(f"doi:{self.doi}")
        if self.oa_pdf:
            tail.append(f"pdf {self.oa_pdf}")
        elif self.url:
            tail.append(self.url)
        tail.append("via " + "+".join(self.sources))
        bits.append("  " + " | ".join(tail))
        return "\n".join(b for b in bits if b)


# ---------------------------------------------------------------------------------------------------------------- http
class _Response:
    """The few attributes this module reads, over urllib (stdlib only: the package's venv carries no requests/httpx)."""

    def __init__(self, status: int, headers, fp):
        self.status_code = status
        self.headers = headers if headers is not None else {}
        self._fp = fp
        self._content: bytes | None = None

    @property
    def content(self) -> bytes:
        if self._content is None:
            self._content = self._fp.read() if self._fp is not None else b""
        return self._content

    def json(self):
        return json.loads(self.content)

    def iter_content(self, n: int):
        while self._fp is not None:
            b = self._fp.read(n)
            if not b:
                break
            yield b


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - each hop is checked by the caller
        return None


def _http_get(url: str, params: dict | None = None, *, stream: bool = False, allow_redirects: bool = True) -> _Response:
    """One GET with a User-Agent and a timeout; one retry on 429/503 (Retry-After honoured up to 10 s). Tests patch this.

    With allow_redirects=False a 3xx comes back as a response carrying Location, so the caller can check each hop."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    opener = urllib.request.build_opener() if allow_redirects else urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    resp = _Response(0, {}, None)
    for attempt in (1, 2):
        try:
            fp = opener.open(req, timeout=TIMEOUT_S)
            return _Response(fp.status, fp.headers, fp)
        except urllib.error.HTTPError as e:
            resp = _Response(e.code, e.headers, e)
            if e.code in (429, 503) and attempt == 1:
                try:
                    wait = min(10.0, float((e.headers or {}).get("Retry-After") or 2))
                except ValueError:
                    wait = 2.0
                time.sleep(wait)
                continue
            return resp
    return resp


def _json(url: str, params: dict | None = None) -> dict:
    r = _http_get(url, params)
    host = urlparse(url).netloc
    if r.status_code == 202 and b"not a bot" in r.content[:4000]:
        raise RuntimeError(f"bot wall (Anubis proof-of-work page, HTTP 202) at {host}")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} from {host}")
    return r.json()


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z]+", " ", (s or "").lower())).strip()


def _clean_doi(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip()
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d, flags=re.I)
    return d.lower() or None


def _year_ok(y: int | None, year_min: int | None, year_max: int | None) -> bool:
    if y is None:
        return year_min is None and year_max is None
    return (year_min is None or y >= year_min) and (year_max is None or y <= year_max)


# ------------------------------------------------------------------------------------------------------------ sources
def _abstract_from_inverted(inv: dict | None) -> str | None:
    if not inv:
        return None
    pos = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    return " ".join(w for _, w in sorted(pos)) or None


def _openalex_paper(w: dict) -> Paper:
    loc = w.get("primary_location") or {}
    best = w.get("best_oa_location") or {}
    return Paper(
        title=(w.get("display_name") or w.get("title") or "").strip(),
        year=w.get("publication_year"),
        venue=((loc.get("source") or {}).get("display_name")),
        authors=[a["author"]["display_name"] for a in (w.get("authorships") or []) if a.get("author")],
        doi=_clean_doi(w.get("doi")),
        url=w.get("id"),
        citations=w.get("cited_by_count"),
        oa_pdf=best.get("pdf_url") or (w.get("open_access") or {}).get("oa_url"),
        abstract=_abstract_from_inverted(w.get("abstract_inverted_index")),
        sources=["openalex"],
    )


_OA_SELECT = ("id,display_name,publication_year,primary_location,best_oa_location,cited_by_count,authorships,"
              "open_access,doi,abstract_inverted_index")


def search_openalex(q: str, limit: int = 10, year_min: int | None = None, year_max: int | None = None) -> list[Paper]:
    params = {"search": q, "per-page": min(50, max(25, limit * 3)), "select": _OA_SELECT}  # wide pool: merge ranks
    filt = []
    if year_min:
        filt.append(f"from_publication_date:{year_min}-01-01")
    if year_max:
        filt.append(f"to_publication_date:{year_max}-12-31")
    if filt:
        params["filter"] = ",".join(filt)
    if os.environ.get("SCRAPERX_MAILTO"):
        params["mailto"] = os.environ["SCRAPERX_MAILTO"]
    j = _json("https://api.openalex.org/works", params)
    return [_openalex_paper(w) for w in j.get("results", [])]


def search_crossref(q: str, limit: int = 10, year_min: int | None = None, year_max: int | None = None) -> list[Paper]:
    params: dict = {"query.bibliographic": q, "rows": min(50, max(20, limit * 3)),
                    "select": "DOI,title,author,issued,container-title,is-referenced-by-count,link,URL"}
    filt = []
    if year_min:
        filt.append(f"from-pub-date:{year_min}")
    if year_max:
        filt.append(f"until-pub-date:{year_max}")
    if filt:
        params["filter"] = ",".join(filt)
    if os.environ.get("SCRAPERX_MAILTO"):
        params["mailto"] = os.environ["SCRAPERX_MAILTO"]
    j = _json("https://api.crossref.org/works", params)
    out = []
    for it in (j.get("message") or {}).get("items") or []:
        parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        year = parts[0] if parts and isinstance(parts[0], int) else None
        out.append(Paper(
            title=" ".join(it.get("title") or []).strip(),
            year=year,
            venue=" ".join(it.get("container-title") or []) or None,
            authors=[" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in it.get("author") or []],
            doi=_clean_doi(it.get("DOI")),
            url=it.get("URL"),
            citations=it.get("is-referenced-by-count"),
            oa_pdf=None,  # Crossref's PDF links are publisher links, mostly paywalled; read() asks OpenAlex/S2 for open copies
            sources=["crossref"],
        ))
    return out


def search_dblp(q: str, limit: int = 10, year_min: int | None = None, year_max: int | None = None) -> list[Paper]:
    j = _json("https://dblp.org/search/publ/api", {"q": q, "format": "json", "h": max(1, min(limit * 2, 100))})
    hits = ((j.get("result") or {}).get("hits") or {}).get("hit") or []
    out = []
    for h in hits:
        info = h.get("info") or {}
        au = (info.get("authors") or {}).get("author") or []
        if isinstance(au, dict):
            au = [au]
        y_raw = str(info.get("year") or "")
        year = int(y_raw) if y_raw.isdigit() else None
        if not _year_ok(year, year_min, year_max):
            continue
        out.append(Paper(
            title=re.sub(r"\.$", "", (info.get("title") or "").strip()),
            year=year,
            venue=info.get("venue") if isinstance(info.get("venue"), str) else ", ".join(info.get("venue") or []) or None,
            authors=[re.sub(r" \d{4}$", "", a.get("text", "")) for a in au if isinstance(a, dict)],
            doi=_clean_doi(info.get("doi")),
            url=info.get("ee") if isinstance(info.get("ee"), str) else info.get("url"),
            sources=["dblp"],
        ))
        if len(out) >= limit:
            break
    return out


def search_arxiv(q: str, limit: int = 10, year_min: int | None = None, year_max: int | None = None) -> list[Paper]:
    terms = " AND ".join(f"all:{t}" for t in re.findall(r"[0-9A-Za-z\-]+", q)[:8]) or f"all:{q}"
    r = _http_get("https://export.arxiv.org/api/query",
                  {"search_query": terms, "start": 0, "max_results": max(1, min(limit * 2, 50))})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} from export.arxiv.org")
    root = safe_fromstring(r.content)
    out = []
    for e in root.findall(f"{_ATOM}entry"):
        published = (e.findtext(f"{_ATOM}published") or "")[:4]
        year = int(published) if published.isdigit() else None
        if not _year_ok(year, year_min, year_max):
            continue
        pdf = None
        for link in e.findall(f"{_ATOM}link"):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf = link.get("href")
        doi = e.findtext("{http://arxiv.org/schemas/atom}doi")
        out.append(Paper(
            title=re.sub(r"\s+", " ", e.findtext(f"{_ATOM}title") or "").strip(),
            year=year,
            venue="arXiv",
            authors=[a.findtext(f"{_ATOM}name") or "" for a in e.findall(f"{_ATOM}author")],
            doi=_clean_doi(doi),
            url=e.findtext(f"{_ATOM}id"),
            oa_pdf=pdf,
            abstract=re.sub(r"\s+", " ", e.findtext(f"{_ATOM}summary") or "").strip() or None,
            sources=["arxiv"],
        ))
        if len(out) >= limit:
            break
    return out


_S2_FIELDS = "title,year,venue,authors,citationCount,openAccessPdf,externalIds,abstract,url"


def _s2_paper(p: dict) -> Paper:
    return Paper(
        title=(p.get("title") or "").strip(),
        year=p.get("year"),
        venue=p.get("venue") or None,
        authors=[a.get("name", "") for a in (p.get("authors") or [])],
        doi=_clean_doi((p.get("externalIds") or {}).get("DOI")),
        url=p.get("url"),
        citations=p.get("citationCount"),
        oa_pdf=(p.get("openAccessPdf") or {}).get("url") or None,
        abstract=p.get("abstract"),
        sources=["s2"],
    )


def search_s2(q: str, limit: int = 10, year_min: int | None = None, year_max: int | None = None) -> list[Paper]:
    params = {"query": q, "limit": max(1, min(limit, 100)), "fields": _S2_FIELDS}
    if year_min or year_max:
        params["year"] = f"{year_min or ''}-{year_max or ''}"
    j = _json("https://api.semanticscholar.org/graph/v1/paper/search", params)
    return [_s2_paper(p) for p in (j.get("data") or [])]


_SEARCHERS = {"openalex": search_openalex, "crossref": search_crossref, "dblp": search_dblp, "arxiv": search_arxiv,
              "s2": search_s2}


# ------------------------------------------------------------------------------------------------------------- merge
def merge(papers: list[Paper]) -> list[Paper]:
    """One row per work: the same DOI, or the same normalised title with years at most 1 apart (preprint vs print)."""
    out: list[Paper] = []
    for p in papers:
        twin = None
        for m in out:
            if p.doi and m.doi and p.doi == m.doi:
                twin = m
                break
            if _norm(p.title) and _norm(p.title) == _norm(m.title) and (
                    p.year is None or m.year is None or abs(p.year - m.year) <= 1):
                twin = m
                break
        if twin is None:
            out.append(Paper(**{**asdict(p), "authors": list(p.authors), "sources": list(p.sources)}))
            continue
        for f in ("venue", "doi", "url", "oa_pdf", "abstract"):
            if not getattr(twin, f) and getattr(p, f):
                setattr(twin, f, getattr(p, f))
        if not twin.authors and p.authors:
            twin.authors = list(p.authors)
        if p.year and (twin.year is None or p.year < twin.year):
            twin.year = p.year  # the earliest year is the publication; later ones are reprints or index dates
        if p.citations is not None:
            twin.citations = max(twin.citations or 0, p.citations)
        for s in p.sources:
            if s not in twin.sources:
                twin.sources.append(s)
    out.sort(key=lambda p: (-(p.citations or 0), p.year or 9999))
    return out


def search(q: str, sources=SOURCES, limit: int = 10, year_min: int | None = None,
           year_max: int | None = None) -> tuple[list[Paper], dict[str, str]]:
    found: list[Paper] = []
    errors: dict[str, str] = {}
    for s in sources:
        try:
            found.extend(_SEARCHERS[s](q, limit, year_min, year_max))
        except Exception as e:  # a source that fails is named, never read as "no papers"
            errors[s] = f"{type(e).__name__}: {e}"[:200]
    return merge(found)[:limit], errors


# -------------------------------------------------------------------------------------------------------------- cite
def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def cite(title: str, sources=("openalex", "crossref")) -> dict:
    """The best title match across sources: verdict 'match' (>= 0.92), 'close' (>= 0.75) or 'no match'."""
    papers, errors = search(title, sources=sources, limit=8)
    scored = sorted(((similarity(title, p.title), p) for p in papers), key=lambda x: -x[0])
    if not scored:
        return {"verdict": "no match", "score": 0.0, "paper": None, "errors": errors}
    score, best = scored[0]
    verdict = "match" if score >= 0.92 else "close" if score >= 0.75 else "no match"
    return {"verdict": verdict, "score": round(score, 3), "paper": asdict(best), "errors": errors}


# -------------------------------------------------------------------------------------------------------------- read
_ARXIV_ID = re.compile(r"^(arxiv:)?(\d{4}\.\d{4,5}(v\d+)?|[a-z\-]+(\.[A-Z]{2})?/\d{7}(v\d+)?)$", re.I)


def _public_url(url: str) -> bool:
    """http(s) only, and every address the host resolves to is public (no loopback, private, link-local, reserved)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def pdf_candidates(ref: str) -> tuple[list[str], Paper | None]:
    """Open-access PDF URLs for a DOI, an arXiv id or a title, best first."""
    ref = ref.strip()
    if _ARXIV_ID.match(ref):
        aid = re.sub(r"^arxiv:", "", ref, flags=re.I)
        return [f"https://arxiv.org/pdf/{aid}"], None
    doi = _clean_doi(ref) if re.match(r"^(https?://(dx\.)?doi\.org/|doi:)?10\.\d{4,9}/", ref, re.I) else None
    paper = None
    urls: list[str] = []
    if doi:
        try:
            w = _json(f"https://api.openalex.org/works/doi:{doi}", {"select": _OA_SELECT})
            paper = _openalex_paper(w)
            for loc in [w.get("best_oa_location") or {}] + list(w.get("locations") or []):
                if loc.get("pdf_url"):
                    urls.append(loc["pdf_url"])
            if (w.get("open_access") or {}).get("oa_url"):
                urls.append(w["open_access"]["oa_url"])
        except Exception:
            pass
        try:
            p = _s2_paper(_json(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}", {"fields": _S2_FIELDS}))
            paper = paper or p
            if p.oa_pdf:
                urls.append(p.oa_pdf)
        except Exception:
            pass
    else:
        c = cite(ref, sources=("openalex", "crossref", "arxiv"))
        if c["paper"] and c["verdict"] != "no match":
            paper = Paper(**c["paper"])
            if paper.oa_pdf:
                urls.append(paper.oa_pdf)
            if paper.doi:
                more, _ = pdf_candidates(paper.doi)
                urls.extend(more)
    seen, uniq = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq, paper


def _download_pdf(url: str, dest: Path) -> tuple[bool, str]:
    hops = 0
    while hops < 6:
        if not _public_url(url):
            return False, f"refused non-public or non-http url {url}"
        r = _http_get(url, stream=True, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            url = urljoin(url, r.headers["Location"])
            hops += 1
            continue
        if r.status_code != 200:
            return False, f"HTTP {r.status_code} from {urlparse(url).netloc}"
        got = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                got += len(chunk)
                if got > PDF_MAX_BYTES:
                    f.close()
                    dest.unlink(missing_ok=True)
                    return False, f"larger than {PDF_MAX_BYTES} B"
                f.write(chunk)
        with open(dest, "rb") as f:
            magic = f.read(5)
        if magic != b"%PDF-":
            dest.unlink(missing_ok=True)
            return False, f"not a PDF (an HTML landing page or a bot wall) from {urlparse(url).netloc}"
        return True, url
    return False, "too many redirects"


def read(ref: str, out_dir: str | Path | None = None) -> dict:
    out = Path(out_dir or Path.home() / ".scraperx" / "papers")
    out.mkdir(parents=True, exist_ok=True)
    urls, paper = pdf_candidates(ref)
    slug = re.sub(r"[^0-9a-z]+", "-", (paper.doi or paper.title if paper else ref).lower()).strip("-")[:80] or "paper"
    tried = []
    for u in urls:
        dest = out / f"{slug}.pdf"
        ok, why = _download_pdf(u, dest)
        tried.append({"url": u, "ok": ok, "why": why})
        if not ok:
            continue
        from scraperx.pdf_text_parser import extract_text_per_page

        pages, method = extract_text_per_page(dest)
        text = "\n\f".join(pages)
        txt = dest.with_suffix(".txt")
        txt.write_text(text)
        return {"ok": True, "pdf": str(dest), "txt": str(txt), "pages": len(pages), "chars": len(text),
                "method": method, "paper": asdict(paper) if paper else None, "tried": tried}
    return {"ok": False, "paper": asdict(paper) if paper else None, "tried": tried,
            "why": "no open-access PDF could be downloaded" if urls else "no open-access PDF is known for this reference"}


# --------------------------------------------------------------------------------------------------------------- cli
def main_papers(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "papers":
        argv = argv[1:]
    ap = argparse.ArgumentParser(prog="scraperx papers",
                                 description="Find, verify and read scholarly papers from keyless open indexes "
                                             "(OpenAlex, Crossref, arXiv, Semantic Scholar; dblp by name).")
    sub = ap.add_subparsers(dest="action", required=True)
    s = sub.add_parser("search", help="papers matching a query, merged across sources, most cited first")
    s.add_argument("query", nargs="+")
    s.add_argument("-n", "--limit", type=int, default=10)
    s.add_argument("--before", type=int, help="published in or before this year (e.g. 2010 for pre-AI classics)")
    s.add_argument("--after", type=int, help="published in or after this year")
    s.add_argument("--source", default="all", help="all (= " + ", ".join(SOURCES) + "), or a comma list of "
                   + ", ".join(ALL_SOURCES))
    s.add_argument("--abstract", action="store_true", help="print the abstract under each paper")
    s.add_argument("--json", action="store_true")
    c = sub.add_parser("cite", help="verify a citation: the best title match and a similarity score")
    c.add_argument("title", nargs="+")
    c.add_argument("--json", action="store_true")
    r = sub.add_parser("read", help="download an open-access PDF (DOI, arXiv id or title) and extract its text")
    r.add_argument("ref", nargs="+")
    r.add_argument("--out", help="directory for the PDF and the .txt (default ~/.scraperx/papers)")
    r.add_argument("--chars", type=int, default=1500, help="characters of text to print (default 1500)")
    r.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.action == "search":
        srcs = SOURCES if a.source == "all" else tuple(x.strip() for x in a.source.split(",") if x.strip())
        bad = [x for x in srcs if x not in ALL_SOURCES]
        if bad:
            ap.error(f"unknown source(s): {', '.join(bad)}")
        papers, errors = search(" ".join(a.query), sources=srcs, limit=a.limit, year_min=a.after, year_max=a.before)
        if a.json:
            print(json.dumps({"papers": [asdict(p) for p in papers], "errors": errors}, ensure_ascii=False, indent=1))
        else:
            for i, p in enumerate(papers, 1):
                print(f"{i}. {p.line()}")
                if a.abstract and p.abstract:
                    print("   " + p.abstract[:900])
            for k, v in errors.items():
                print(f"! {k}: {v}", file=sys.stderr)
            if not papers:
                print("no papers found" + (" (every source failed)" if len(errors) == len(srcs) else ""))
        return 0 if papers or len(errors) < len(srcs) else 2
    if a.action == "cite":
        res = cite(" ".join(a.title))
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print(f"{res['verdict']} (similarity {res['score']})")
            if res["paper"]:
                print(Paper(**res["paper"]).line())
            for k, v in res["errors"].items():
                print(f"! {k}: {v}", file=sys.stderr)
        return 0 if res["verdict"] != "no match" else 1
    res = read(" ".join(a.ref), a.out)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif res["ok"]:
        print(f"{res['pages']} pages, {res['chars']} chars ({res['method']}): {res['pdf']}\ntext: {res['txt']}\n")
        print(Path(res["txt"]).read_text()[: a.chars])
    else:
        print(f"not read: {res['why']}")
        for t in res["tried"]:
            print(f"  {t['url']}: {t['why']}")
        if res["paper"]:
            print(Paper(**res["paper"]).line())
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main_papers())
