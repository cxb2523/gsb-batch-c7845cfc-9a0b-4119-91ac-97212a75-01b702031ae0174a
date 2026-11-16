"""Tests for scraperx.docs_crawler — exhaustive docs-site crawl."""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from scraperx.docs_crawler import (
    MIN_PROSE_CHARS,
    SitemapCoverage,
    _LessAggressiveExtractor,
    _parse_sitemap,
    _safe_slug,
    _validate_url,
    crawl,
    extract_text,
    iter_pages,
)


# ── extract_text / _LessAggressiveExtractor ──────────────────────────────────

def test_extract_text_recovers_paragraphs():
    html = """
    <html><head><title>X</title><script>var a=1;</script></head>
    <body>
      <main>
        <h1>Title</h1>
        <p>First paragraph.</p>
        <p>Second paragraph.</p>
      </main>
    </body></html>
    """
    text = extract_text(html)
    assert "Title" in text
    assert "First paragraph" in text
    assert "Second paragraph" in text


def test_extract_text_strips_script_and_style():
    html = """
    <html><body>
      <script>const SECRET = "leaked";</script>
      <style>body { display: none }</style>
      <p>Visible content.</p>
    </body></html>
    """
    text = extract_text(html)
    assert "Visible content" in text
    assert "SECRET" not in text
    assert "leaked" not in text
    assert "display" not in text


def test_extract_text_keeps_nav_and_breadcrumbs():
    """Nav/header/footer often carry context (breadcrumbs, sidebar) — keep them.
    This is the bug-fix from 2026-05-03 IC docs incident."""
    html = """
    <html><body>
      <nav>Home > API > Catalog</nav>
      <header>Documentation v1.0</header>
      <main><p>Body.</p></main>
      <footer>© 2024</footer>
    </body></html>
    """
    text = extract_text(html)
    assert "Home" in text and "Catalog" in text  # breadcrumb survived
    assert "Documentation" in text                # header survived
    assert "Body" in text


def test_extract_text_recovery_threshold_for_real_docs_html():
    """A realistic Docusaurus-like page should yield well above the
    MIN_PROSE_CHARS threshold so it isn't flagged as a shell."""
    paragraph = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 20
    html = (
        "<html><body><main>"
        f"<h1>Catalog</h1><p>{paragraph}</p>"
        f"<p>{paragraph}</p>"
        "</main></body></html>"
    )
    text = extract_text(html)
    assert len(text) > MIN_PROSE_CHARS


def test_extractor_handles_nested_blocks_without_runaway_newlines():
    html = "<div><div><div><p>x</p></div></div></div>"
    text = extract_text(html)
    # Regex collapses 3+ newlines to 2; should not have huge whitespace run.
    assert not re.search(r"\n{3,}", text)


# ── _parse_sitemap ────────────────────────────────────────────────────────────

def test_parse_sitemap_with_namespace():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc></url>
  <url><loc>https://example.com/b</loc></url>
</urlset>"""
    urls = _parse_sitemap(xml)
    assert urls == ["https://example.com/a", "https://example.com/b"]


def test_parse_sitemap_namespace_agnostic_fallback():
    """Some docs sites omit the standard sitemap namespace declaration."""
    xml = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://example.com/a</loc></url>
  <url><loc>https://example.com/b</loc></url>
</urlset>"""
    urls = _parse_sitemap(xml)
    assert urls == ["https://example.com/a", "https://example.com/b"]


def test_parse_sitemap_handles_invalid_xml():
    assert _parse_sitemap("<not valid xml") == []


def test_parse_sitemap_filters_blank_locs():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc></url>
  <url><loc>   </loc></url>
  <url><loc></loc></url>
</urlset>"""
    urls = _parse_sitemap(xml)
    assert urls == ["https://example.com/a"]


# ── _validate_url ─────────────────────────────────────────────────────────────

def test_validate_url_accepts_http_and_https():
    assert _validate_url("https://example.com/x") == "https://example.com/x"
    assert _validate_url("http://example.com/y") == "http://example.com/y"


def test_validate_url_rejects_missing_scheme():
    with pytest.raises(ValueError, match="must start with"):
        _validate_url("example.com")


def test_validate_url_rejects_dash_prefix_flag_injection():
    """Defends against curl flag injection. A URL starting with `-` would
    be interpreted as a curl flag if not validated."""
    with pytest.raises(ValueError):
        _validate_url("--proto-default=https")


def test_validate_url_rejects_empty_and_none():
    with pytest.raises(ValueError):
        _validate_url("")
    with pytest.raises(ValueError):
        _validate_url(None)  # type: ignore[arg-type]


def test_validate_url_rejects_line_breaks():
    with pytest.raises(ValueError, match="line breaks"):
        _validate_url("https://example.com/\nX-Header: evil")


# ── _safe_slug ────────────────────────────────────────────────────────────────

def test_safe_slug_replaces_separators():
    slug = _safe_slug("https://docs.example.com/api/catalog/products",
                      "https://docs.example.com")
    assert slug == "api__catalog__products"


def test_safe_slug_uses_root_marker_for_empty_path():
    slug = _safe_slug("https://docs.example.com/", "https://docs.example.com")
    assert slug == "_root"


def test_safe_slug_strips_dangerous_chars():
    slug = _safe_slug("https://docs.example.com/a%20b/c.html",
                      "https://docs.example.com")
    # `%` is replaced with `_`; only alphanumerics + . _ - survive
    assert "%" not in slug
    assert ".html" in slug or "html" in slug


# ── crawl() integration ──────────────────────────────────────────────────────

def test_crawl_writes_digest_and_uses_supplied_urls(tmp_path: Path):
    """Smoke-test the full crawl pipeline with a stub curl and explicit URL list."""

    def fake_curl_to_file(url, out_path, *, user_agent, timeout):
        out_path.write_text(
            f"<html><body><main><h1>{url}</h1><p>Content for {url}.</p></main></body></html>",
            encoding="utf-8",
        )
        return 200

    with patch("scraperx.docs_crawler._curl_to_file", side_effect=fake_curl_to_file):
        result = crawl(
            "https://example.com",
            tmp_path,
            urls=["https://example.com/a", "https://example.com/b"],
            sleep_between=0,
        )

    assert result.fetched == 2
    assert result.errors == 0
    assert (tmp_path / "_DIGEST.md").exists()
    assert (tmp_path / "_SHELLS.md").exists()
    assert (tmp_path / "a.html").exists()
    assert (tmp_path / "a.txt").exists()
    digest = (tmp_path / "_DIGEST.md").read_text()
    assert "https://example.com/a" in digest
    assert "https://example.com/b" in digest


def test_crawl_skips_url_encoded_duplicates_by_default(tmp_path: Path):
    def fake(url, out_path, *, user_agent, timeout):
        out_path.write_text(f"<p>{url}</p>", encoding="utf-8")
        return 200

    with patch("scraperx.docs_crawler._curl_to_file", side_effect=fake):
        result = crawl(
            "https://example.com",
            tmp_path,
            urls=[
                "https://example.com/v/1.0%20-%20Start",
                "https://example.com/v/1.0",
                "https://example.com/v/1.1",
            ],
            sleep_between=0,
        )

    # %20 URL is skipped
    assert result.fetched == 2
    fetched_urls = {p.url for p in result.pages}
    assert "https://example.com/v/1.0" in fetched_urls
    assert not any("%20" in u for u in fetched_urls)


def test_crawl_flags_shell_pages(tmp_path: Path):
    def fake(url, out_path, *, user_agent, timeout):
        if "thin" in url:
            out_path.write_text("<p>x</p>", encoding="utf-8")
        else:
            out_path.write_text(
                "<main><p>" + ("Real content. " * 200) + "</p></main>",
                encoding="utf-8",
            )
        return 200

    with patch("scraperx.docs_crawler._curl_to_file", side_effect=fake):
        result = crawl(
            "https://example.com",
            tmp_path,
            urls=["https://example.com/thin", "https://example.com/full"],
            sleep_between=0,
        )

    assert result.shells == 1
    shells_md = (tmp_path / "_SHELLS.md").read_text()
    assert "thin" in shells_md


def test_crawl_rejects_non_http_root_url(tmp_path: Path):
    with pytest.raises(ValueError):
        crawl("ftp://example.com", tmp_path, urls=["ftp://example.com/x"])


def test_iter_pages_yields_slug_text_pairs(tmp_path: Path):
    (tmp_path / "page-a.txt").write_text("Alpha content")
    (tmp_path / "page-b.txt").write_text("Beta content")
    (tmp_path / "_DIGEST.md").write_text("ignore me")

    pairs = dict(iter_pages(tmp_path))
    assert pairs == {"page-a": "Alpha content", "page-b": "Beta content"}
    assert "_DIGEST" not in pairs  # underscore-prefixed files are skipped


# ── sitemap INDEX + domain-root fallback (both found LIVE, 2026-08-02) ────────
#
# Two silent defects, neither visible from reading the code — both surfaced the
# moment the crawler was pointed at real sites:
#
# 1. `_parse_sitemap`'s docstring CLAIMED it "handles both single sitemap and
#    sitemap_index". It did not: it harvested every <loc> regardless of whether
#    the parent was <url> (a page) or <sitemap> (a CHILD SITEMAP). On
#    portal.thirdweb.com that meant downloading one sub-sitemap XML file AS a
#    docs page and printing "Crawled 1 pages, 0 errors" for a site with
#    thousands. A success message laid over a near-empty result.
#
# 2. `discover_sitemap` only probed <root>/sitemap.xml. Given a docs root that is
#    a SUBPATH (https://code.claude.com/docs/en/) it found nothing and raised —
#    while https://code.claude.com/sitemap.xml had 2011 URLs sitting right there.

import scraperx.docs_crawler as dc  # noqa: E402

SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-0.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>
</sitemapindex>"""

CHILD_0_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc></url>
  <url><loc>https://example.com/b</loc></url>
</urlset>"""

CHILD_1_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/c</loc></url>
  <url><loc>https://example.com/a</loc></url>
</urlset>"""


def test_parse_sitemap_returns_no_pages_for_an_index():
    """THE regression test: an index holds pointers, not pages.

    Returning the child-sitemap URLs from here is exactly what made the crawler
    fetch a sitemap XML file and count it as a documentation page.
    """
    assert dc._parse_sitemap(SITEMAP_INDEX_XML) == []


def test_is_sitemap_index_discriminates():
    assert dc._is_sitemap_index(SITEMAP_INDEX_XML) is True
    assert dc._is_sitemap_index(CHILD_0_XML) is False
    assert dc._is_sitemap_index("<not valid xml") is False


def test_expand_sitemap_follows_index_children(monkeypatch):
    fetched = []

    def fake_curl(url, **kw):
        fetched.append(url)
        return {
            "https://example.com/sitemap-0.xml": CHILD_0_XML,
            "https://example.com/sitemap-1.xml": CHILD_1_XML,
        }[url]

    monkeypatch.setattr(dc, "_curl_text", fake_curl)
    urls = dc._expand_sitemap(
        SITEMAP_INDEX_XML, "https://example.com/sitemap.xml",
        user_agent="UA", timeout=5,
    )
    assert len(fetched) == 2, "both child sitemaps must be fetched"
    assert urls == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ], "pages from all children, de-duplicated, order preserved"


def test_expand_sitemap_survives_one_bad_child(monkeypatch):
    def fake_curl(url, **kw):
        if url.endswith("sitemap-0.xml"):
            raise RuntimeError("404")
        return CHILD_1_XML

    monkeypatch.setattr(dc, "_curl_text", fake_curl)
    urls = dc._expand_sitemap(
        SITEMAP_INDEX_XML, "https://example.com/sitemap.xml",
        user_agent="UA", timeout=5,
    )
    assert urls == ["https://example.com/c", "https://example.com/a"], (
        "one unreachable child must not sink the whole discovery"
    )


def test_expand_sitemap_depth_is_bounded(monkeypatch):
    """A self-referential index must not spin forever."""
    loop = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/loop.xml</loc></sitemap>
</sitemapindex>"""
    calls = {"n": 0}

    def fake_curl(url, **kw):
        calls["n"] += 1
        return loop

    monkeypatch.setattr(dc, "_curl_text", fake_curl)
    urls = dc._expand_sitemap(
        loop, "https://example.com/sitemap.xml", user_agent="UA", timeout=5,
    )
    assert urls == []
    assert calls["n"] <= dc.MAX_SITEMAP_DEPTH + 1, "recursion must be bounded"


def test_discover_sitemap_falls_back_to_domain_root(monkeypatch):
    """A docs root that is a SUBPATH must still find the domain-root sitemap."""
    tried = []

    def fake_curl(url, **kw):
        tried.append(url)
        if url == "https://example.com/sitemap.xml":
            return CHILD_0_XML
        raise RuntimeError("404")

    monkeypatch.setattr(dc, "_curl_text", fake_curl)
    used, urls = dc.discover_sitemap("https://example.com/docs/en/")
    assert used == "https://example.com/sitemap.xml"
    assert urls == ["https://example.com/a", "https://example.com/b"]
    assert tried[0] == "https://example.com/docs/en/sitemap.xml", (
        "the specific path must still be tried FIRST — the root is a fallback"
    )


# ── sitemap-index coverage accounting (2026-11) ──────────────────────────────
#
# Until this change a child sitemap that failed to fetch produced only a warning
# log line and was silently skipped: _DIGEST.md still read "Pages crawled: N"
# with no way to tell whether N was the whole site or a partial result after a
# missing sub-sitemap. These tests pin the three new coverage facts — children
# attempted, children succeeded, and a URL+reason failure list — and the rule
# that ONLY <sitemapindex> children are ever counted as sitemaps.

COVERAGE_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-0.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>
</sitemapindex>"""


def _expand_with_coverage(monkeypatch, fake_curl, xml=COVERAGE_INDEX_XML,
                          index_url="https://example.com/sitemap.xml"):
    monkeypatch.setattr(dc, "_curl_text", fake_curl)
    coverage = SitemapCoverage()
    urls = dc._expand_sitemap(
        xml, index_url, user_agent="UA", timeout=5, _coverage=coverage,
    )
    return urls, coverage


def test_expand_coverage_all_children_succeed(monkeypatch):
    """All children reachable: empty failure list, counters exact."""

    def fake_curl(url, **kw):
        return {
            "https://example.com/sitemap-0.xml": CHILD_0_XML,
            "https://example.com/sitemap-1.xml": CHILD_1_XML,
        }[url]

    urls, coverage = _expand_with_coverage(monkeypatch, fake_curl)

    assert urls == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ], "returned pages, order and dedup must be unchanged"
    assert coverage.sub_sitemaps_attempted == 2
    assert coverage.sub_sitemaps_succeeded == 2
    assert coverage.failures == []


def test_expand_coverage_records_failed_child_but_keeps_other_pages(monkeypatch):
    """One child raises: every reachable page still returned, failure listed
    with URL and an exception-type reason."""

    def fake_curl(url, **kw):
        if url.endswith("sitemap-0.xml"):
            raise RuntimeError("404 Not Found")
        return CHILD_1_XML

    urls, coverage = _expand_with_coverage(monkeypatch, fake_curl)

    # The surviving child's pages come back in full, order preserved.
    assert urls == ["https://example.com/c", "https://example.com/a"]
    assert coverage.sub_sitemaps_attempted == 2, (
        "both children were attempted — the failed one still counts as expanded"
    )
    assert coverage.sub_sitemaps_succeeded == 1
    assert len(coverage.failures) == 1
    failed = coverage.failures[0]
    assert failed.url == "https://example.com/sitemap-0.xml"
    assert failed.reason.startswith("RuntimeError"), (
        "reason must carry the exception type plus a short message"
    )
    assert "404" in failed.reason


def test_expand_coverage_self_referential_index_hits_depth_limit(monkeypatch):
    """A self-referential index must terminate AND record the depth cut as a
    failed child sitemap rather than returning a silent empty list."""
    loop = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/loop.xml</loc></sitemap>
</sitemapindex>"""
    calls = {"n": 0}

    def fake_curl(url, **kw):
        calls["n"] += 1
        return loop

    urls, coverage = _expand_with_coverage(
        monkeypatch, fake_curl, xml=loop, index_url="https://example.com/sitemap.xml",
    )

    assert urls == []
    assert calls["n"] <= dc.MAX_SITEMAP_DEPTH + 1, "recursion must stay bounded"
    assert coverage.sub_sitemaps_attempted >= 1, (
        "the loop child was really fetched as a sitemapindex child"
    )
    assert coverage.sub_sitemaps_succeeded == coverage.sub_sitemaps_attempted, (
        "every fetch succeeded — the cut is a depth failure, not an HTTP one"
    )
    assert len(coverage.failures) >= 1, "depth truncation must not be silent"
    cut = coverage.failures[-1]
    assert cut.url == "https://example.com/loop.xml"
    assert cut.reason.startswith("SitemapDepthLimitExceeded"), (
        "the depth cut needs an explicit, distinguishable reason"
    )
    assert str(dc.MAX_SITEMAP_DEPTH) in cut.reason


def test_expand_coverage_plain_urlset_counts_nothing(monkeypatch):
    """Page URLs inside a <urlset> are NOT child sitemaps and must never touch
    the counters or failure list."""

    def fake_curl(url, **kw):
        raise AssertionError(f"no further fetch expected, got {url}")

    urls, coverage = _expand_with_coverage(
        monkeypatch, fake_curl, xml=CHILD_0_XML,
        index_url="https://example.com/sitemap.xml",
    )

    assert urls == ["https://example.com/a", "https://example.com/b"]
    assert coverage.sub_sitemaps_attempted == 0
    assert coverage.sub_sitemaps_succeeded == 0
    assert coverage.failures == []


def test_expand_coverage_without_collector_behaves_as_before(monkeypatch):
    """Back-compat: omitting _coverage keeps the plain list-returning API and
    the depth limit surfaces as an empty list, never an uncaught exception."""

    def fake_curl(url, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(dc, "_curl_text", fake_curl)
    urls = dc._expand_sitemap(
        COVERAGE_INDEX_XML, "https://example.com/sitemap.xml",
        user_agent="UA", timeout=5,
    )
    assert urls == []


def test_discover_sitemap_returns_coverage_and_keeps_url_contract(monkeypatch):
    """discover_sitemap keeps its (sitemap_url, urls) tuple shape/order while
    populating the optional coverage collector."""

    def fake_curl(url, **kw):
        return {
            "https://example.com/sitemap.xml": COVERAGE_INDEX_XML,
            "https://example.com/sitemap-0.xml": CHILD_0_XML,
            "https://example.com/sitemap-1.xml": CHILD_1_XML,
        }[url]

    monkeypatch.setattr(dc, "_curl_text", fake_curl)
    coverage = SitemapCoverage()
    used, urls = dc.discover_sitemap("https://example.com", _coverage=coverage)

    assert used == "https://example.com/sitemap.xml"
    assert urls == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    assert coverage.sub_sitemaps_attempted == 2
    assert coverage.sub_sitemaps_succeeded == 2
    assert coverage.failures == []


def test_discover_sitemap_discards_coverage_from_failed_probes(monkeypatch):
    """A first candidate index whose children fail must not leak its counters
    into the coverage of the candidate that actually succeeds."""

    def fake_curl(url, **kw):
        if url == "https://example.com/sitemap.xml":
            # Probed first: an index whose only child 500s and yields no pages.
            return COVERAGE_INDEX_XML
        if url.endswith("sitemap-0.xml") or url.endswith("sitemap-1.xml"):
            raise RuntimeError("500")
        if url == "https://example.com/sitemap_index.xml":
            return CHILD_0_XML
        raise RuntimeError("404")

    monkeypatch.setattr(dc, "_curl_text", fake_curl)
    coverage = SitemapCoverage()
    used, urls = dc.discover_sitemap("https://example.com", _coverage=coverage)

    assert used == "https://example.com/sitemap_index.xml"
    assert urls == ["https://example.com/a", "https://example.com/b"]
    # The winning sitemap is a plain urlset: zero child sitemaps, zero failures —
    # the dead first index's accounting was reset, not merged.
    assert coverage.sub_sitemaps_attempted == 0
    assert coverage.failures == []


def test_crawl_records_sitemap_coverage_and_digest_lines(tmp_path: Path, monkeypatch):
    """End-to-end: coverage lands on CrawlResult and _DIGEST.md always prints
    the three lines, including an explicit 'no failed child sitemaps' row."""
    sitemap_store = {
        "https://example.com/sitemap.xml": COVERAGE_INDEX_XML,
        "https://example.com/sitemap-0.xml": CHILD_0_XML,
        "https://example.com/sitemap-1.xml": CHILD_1_XML,
    }

    def fake_curl_text(url, **kw):
        return sitemap_store[url]

    def fake_curl_to_file(url, out_path, *, user_agent, timeout):
        out_path.write_text(
            "<main><p>" + ("Real content. " * 200) + "</p></main>",
            encoding="utf-8",
        )
        return 200

    monkeypatch.setattr(dc, "_curl_text", fake_curl_text)
    monkeypatch.setattr(dc, "_curl_to_file", fake_curl_to_file)

    result = crawl("https://example.com", tmp_path, sleep_between=0)

    assert result.sub_sitemaps_attempted == 2
    assert result.sub_sitemaps_succeeded == 2
    assert result.failed_sub_sitemaps == []
    # Pages from both children (a, b, c) were crawled.
    assert result.fetched == 3
    digest = (tmp_path / "_DIGEST.md").read_text(encoding="utf-8")
    assert "Child sitemaps expanded" in digest
    assert "Child sitemaps successfully fetched: **2**" in digest
    assert "无失败子地图" in digest, "empty failure list still gets an explicit row"


def test_crawl_digest_lists_failed_child_with_reason(tmp_path: Path, monkeypatch):
    def fake_curl_text(url, **kw):
        if url.endswith("sitemap-0.xml"):
            raise RuntimeError("403 Forbidden")
        if url.endswith("sitemap.xml"):
            return COVERAGE_INDEX_XML
        return CHILD_1_XML

    def fake_curl_to_file(url, out_path, *, user_agent, timeout):
        out_path.write_text(
            "<main><p>" + ("Real content. " * 200) + "</p></main>",
            encoding="utf-8",
        )
        return 200

    monkeypatch.setattr(dc, "_curl_text", fake_curl_text)
    monkeypatch.setattr(dc, "_curl_to_file", fake_curl_to_file)

    result = crawl("https://example.com", tmp_path, sleep_between=0)

    assert result.sub_sitemaps_attempted == 2
    assert result.sub_sitemaps_succeeded == 1
    assert len(result.failed_sub_sitemaps) == 1
    assert result.failed_sub_sitemaps[0].url == "https://example.com/sitemap-0.xml"
    digest = (tmp_path / "_DIGEST.md").read_text(encoding="utf-8")
    assert "Failed child sitemaps: **1**" in digest
    assert "https://example.com/sitemap-0.xml" in digest
    assert "RuntimeError" in digest
    assert "403" in digest
    assert "无失败子地图" not in digest


def test_crawl_explicit_urls_have_zero_sitemap_coverage(tmp_path: Path):
    """With urls=[...] no sitemap walk happens, so coverage stays empty and the
    digest still renders the explicit no-failure row."""

    def fake(url, out_path, *, user_agent, timeout):
        out_path.write_text(
            "<main><p>" + ("Real content. " * 200) + "</p></main>",
            encoding="utf-8",
        )
        return 200

    with patch("scraperx.docs_crawler._curl_to_file", side_effect=fake):
        result = crawl(
            "https://example.com", tmp_path,
            urls=["https://example.com/a"], sleep_between=0,
        )

    assert result.sub_sitemaps_attempted == 0
    assert result.sub_sitemaps_succeeded == 0
    assert result.failed_sub_sitemaps == []
    digest = (tmp_path / "_DIGEST.md").read_text(encoding="utf-8")
    assert "Child sitemaps expanded" in digest
    assert "无失败子地图" in digest


def test_cli_prints_hint_only_when_child_sitemaps_fail(tmp_path: Path, monkeypatch, capsys):
    """The CLI gets one line pointing at the digest's failure list, and only
    when failures exist."""

    def fake_curl_text(url, **kw):
        if url.endswith("sitemap-0.xml"):
            raise RuntimeError("503")
        if url.endswith("sitemap.xml"):
            return COVERAGE_INDEX_XML
        return CHILD_1_XML

    def fake_curl_to_file(url, out_path, *, user_agent, timeout):
        out_path.write_text(
            "<main><p>" + ("Real content. " * 200) + "</p></main>",
            encoding="utf-8",
        )
        return 200

    monkeypatch.setattr(dc, "_curl_text", fake_curl_text)
    monkeypatch.setattr(dc, "_curl_to_file", fake_curl_to_file)
    monkeypatch.setattr(
        "sys.argv",
        ["scraperx", "docs-crawl", "https://example.com", "-o", str(tmp_path),
         "--quiet", "--sleep-between", "0"],
    )

    rc = dc._main_docs_crawl()
    assert rc == 0
    out = capsys.readouterr().out
    assert "child sitemap(s) failed" in out
    assert "_DIGEST.md" in out

    # Now the all-success case: no hint line, but the run still works.
    monkeypatch.setattr(
        "sys.argv",
        ["scraperx", "docs-crawl", "https://example.com", "-o",
         str(tmp_path / "clean"), "--quiet", "--sleep-between", "0"],
    )
    good_store = {
        "https://example.com/sitemap.xml": COVERAGE_INDEX_XML,
        "https://example.com/sitemap-0.xml": CHILD_0_XML,
        "https://example.com/sitemap-1.xml": CHILD_1_XML,
    }
    monkeypatch.setattr(dc, "_curl_text", lambda url, **kw: good_store[url])
    rc = dc._main_docs_crawl()
    assert rc == 0
    out = capsys.readouterr().out
    assert "child sitemap(s) failed" not in out
