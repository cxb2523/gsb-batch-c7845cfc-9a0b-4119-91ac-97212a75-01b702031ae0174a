"""scraperx.papers: parsing, merging, citation verdicts, the URL guard and the CLI, all offline (_http_get is patched)."""

from __future__ import annotations

import json

import pytest

from scraperx import papers as P


class _Resp:
    def __init__(self, status=200, payload=None, content=b"", headers=None):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._payload


OPENALEX = {"results": [{
    "id": "https://openalex.org/W1", "display_name": "No pane, no gain", "publication_year": 2005,
    "primary_location": {"source": {"display_name": "ACM SIGMOD Record"}},
    "best_oa_location": {"pdf_url": None}, "open_access": {"oa_url": None},
    "authorships": [{"author": {"display_name": "Jin Li"}}, {"author": {"display_name": "David Maier"}}],
    "doi": "https://doi.org/10.1145/1058150.1058158", "cited_by_count": 271,
    "abstract_inverted_index": {"panes": [1], "Sliding": [0], "windows": [2]},
}]}

DBLP = {"result": {"hits": {"hit": [
    {"info": {"title": "No pane, no gain: efficient evaluation of sliding-window aggregates over data streams.",
              "year": "2005", "venue": "SIGMOD Rec.", "doi": "10.1145/1058150.1058158",
              "authors": {"author": [{"text": "Jin Li 0001"}, {"text": "David Maier"}]}, "ee": "https://doi.org/x"}},
    {"info": {"title": "A later paper.", "year": "2021", "venue": "VLDB",
              "authors": {"author": {"text": "Solo Author"}}}},
]}}}

CROSSREF = {"message": {"items": [{
    "DOI": "10.1109/ICDE.2003.1260792", "title": ["Efficient maintenance of materialized top-k views"],
    "author": [{"given": "Ke", "family": "Yi"}, {"given": "Hai", "family": "Yu"}],
    "issued": {"date-parts": [[2003, 3]]}, "container-title": ["Proceedings 19th ICDE"], "is-referenced-by-count": 60,
    "URL": "https://doi.org/10.1109/icde.2003.1260792"}]}}

ARXIV = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1503.00001v1</id>
    <published>2015-03-01T00:00:00Z</published>
    <title>General  incremental
      sliding-window aggregation</title>
    <summary>We study windows.</summary>
    <author><name>Kanat Tangwongsan</name></author>
    <link title="pdf" href="http://arxiv.org/pdf/1503.00001v1" rel="related" type="application/pdf"/>
    <arxiv:doi>10.14778/2752939.2752940</arxiv:doi>
  </entry>
</feed>"""


@pytest.fixture
def fake_http(monkeypatch):
    calls = []

    def fake(url, params=None, *, stream=False, allow_redirects=True):
        calls.append((url, params))
        if "openalex" in url:
            return _Resp(payload=OPENALEX)
        if "dblp" in url:
            return _Resp(payload=DBLP)
        if "crossref" in url:
            return _Resp(payload=CROSSREF)
        if "arxiv" in url:
            return _Resp(content=ARXIV)
        return _Resp(status=429)

    monkeypatch.setattr(P, "_http_get", fake)
    return calls


def test_openalex_parse_abstract_and_year_filter(fake_http):
    out = P.search_openalex("panes", limit=5, year_min=1990, year_max=2010)
    assert out[0].title == "No pane, no gain" and out[0].year == 2005 and out[0].citations == 271
    assert out[0].doi == "10.1145/1058150.1058158" and out[0].abstract == "Sliding panes windows"
    assert fake_http[0][1]["filter"] == "from_publication_date:1990-01-01,to_publication_date:2010-12-31"


def test_dblp_parse_single_author_dict_and_year_cut(fake_http):
    out = P.search_dblp("panes", limit=5, year_max=2010)
    assert len(out) == 1 and out[0].authors == ["Jin Li", "David Maier"] and not out[0].title.endswith(".")
    assert P.search_dblp("x", limit=5)[1].authors == ["Solo Author"]


def test_arxiv_atom_parse(fake_http):
    out = P.search_arxiv("sliding window aggregation", limit=3)
    assert out[0].title == "General incremental sliding-window aggregation"
    assert out[0].year == 2015 and out[0].oa_pdf.endswith("1503.00001v1") and out[0].doi == "10.14778/2752939.2752940"


def test_crossref_parse_print_year(fake_http):
    out = P.search_crossref("top-k views", limit=3, year_max=2005)
    assert out[0].year == 2003 and out[0].doi == "10.1109/icde.2003.1260792" and out[0].authors == ["Ke Yi", "Hai Yu"]
    assert "until-pub-date:2005" in fake_http[0][1]["filter"]


def test_anubis_bot_wall_is_named(monkeypatch):
    page = b"<!doctype html><html><head><title>Making sure you&#39;re not a bot!</title>"
    monkeypatch.setattr(P, "_http_get", lambda *a, **k: _Resp(status=202, content=page))
    papers, errors = P.search("x", sources=("dblp",))
    assert papers == [] and "bot wall" in errors["dblp"]


def test_merge_by_doi_keeps_citations_sources_and_earliest_year():
    a = P.Paper(title="No pane, no gain", year=2005, doi="10.1/x", citations=271, sources=["openalex"])
    b = P.Paper(title="No pane, no gain: efficient evaluation", year=2006, doi="10.1/x", venue="SIGMOD Rec.",
                sources=["dblp"])
    c = P.Paper(title="Another", year=1999, citations=900, sources=["s2"])
    m = P.merge([a, b, c])
    assert [p.title for p in m] == ["Another", "No pane, no gain"]
    assert m[1].venue == "SIGMOD Rec." and m[1].sources == ["openalex", "dblp"] and m[1].year == 2005


def test_search_names_a_failing_source_instead_of_hiding_it(fake_http):
    papers, errors = P.search("panes", sources=("openalex", "s2"), limit=5)
    assert papers and "s2" in errors and "429" in errors["s2"]


def test_cite_verdicts(fake_http):
    assert P.cite("No pane, no gain")["verdict"] == "match"
    assert P.cite("Completely unrelated title about cats and dogs")["verdict"] == "no match"


@pytest.mark.parametrize("url,ok", [
    ("file:///etc/passwd", False), ("http://127.0.0.1/x.pdf", False), ("http://10.0.0.5/a.pdf", False),
    ("http://169.254.169.254/latest", False), ("ftp://example.com/a.pdf", False),
])
def test_public_url_guard(url, ok):
    assert P._public_url(url) is ok


def test_pdf_candidates_arxiv_id_needs_no_network(monkeypatch):
    monkeypatch.setattr(P, "_http_get", lambda *a, **k: pytest.fail("no network expected"))
    urls, paper = P.pdf_candidates("arXiv:1503.00001v1")
    assert urls == ["https://arxiv.org/pdf/1503.00001v1"] and paper is None


def test_download_refuses_html_landing_page(monkeypatch, tmp_path):
    class R(_Resp):
        def iter_content(self, n):
            yield b"<html>bot wall</html>"

    monkeypatch.setattr(P, "_public_url", lambda u: True)
    monkeypatch.setattr(P, "_http_get", lambda *a, **k: R())
    ok, why = P._download_pdf("https://example.com/a.pdf", tmp_path / "a.pdf")
    assert not ok and "not a PDF" in why and not (tmp_path / "a.pdf").exists()


def test_cli_search_json(fake_http, capsys):
    rc = P.main_papers(["papers", "search", "panes", "--source", "openalex,crossref", "--before", "2010", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["papers"][0]["doi"] == "10.1145/1058150.1058158" and out["errors"] == {}
    assert set(out["papers"][0]["sources"]) == {"openalex"}


def test_cli_rejects_unknown_source(fake_http):
    with pytest.raises(SystemExit):
        P.main_papers(["search", "x", "--source", "google"])
