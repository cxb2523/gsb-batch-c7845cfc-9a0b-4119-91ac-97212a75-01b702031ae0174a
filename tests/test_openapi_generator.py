"""Preprocessing must tolerate per-page failures and keep the cache clean."""

import asyncio

import pytest

from airbyte_connector_generator_poc import openapi_generator
from airbyte_connector_generator_poc.errors import NoUsablePagesError
from airbyte_connector_generator_poc.report import PageReport

from .conftest import md5_of

URL_OK_1 = "https://docs.example.com/intro"
URL_OK_2 = "https://docs.example.com/search"
URL_BAD = "https://docs.example.com/not-a-page"

MAIN_SELECTOR = "#content"
IRRELEVANT_SELECTORS = ["#nav", "#footer"]


PAGE_TEMPLATE = """
<html>
  <body>
    <nav id="nav">Navigation</nav>
    <main id="content">
      <h1>{heading}</h1>
      <p>{body}</p>
    </main>
    <footer id="footer">Footer</footer>
  </body>
</html>
"""


def _page(heading: str, body: str) -> str:
    return PAGE_TEMPLATE.format(heading=heading, body=body)


@pytest.fixture
def fake_extraction(monkeypatch):
    """Replace the LLM backed selector election with deterministic selectors."""
    calls = {"main": [], "irrelevant": []}

    def fake_extract(raw_html, main_section_selector=None,
                     irrelevant_sections_selectors=None):
        from bs4 import BeautifulSoup
        from airbyte_connector_generator_poc.docs_parser import remove_irrelevant_sections

        if main_section_selector is None:
            calls["main"].append(raw_html[:30])
            main_section_selector = MAIN_SELECTOR
        if irrelevant_sections_selectors is None:
            calls["irrelevant"].append(raw_html[:30])
            irrelevant_sections_selectors = IRRELEVANT_SELECTORS

        soup = BeautifulSoup(raw_html, "html.parser")
        main = soup.select_one(main_section_selector)
        if main is None:
            raise ValueError(
                f"No main content found with selector `{main_section_selector}`.")
        cleaned = remove_irrelevant_sections(
            main, irrelevant_sections_selectors)
        return cleaned, main_section_selector, irrelevant_sections_selectors

    monkeypatch.setattr(openapi_generator, "extract_relevant_html",
                        fake_extract)
    return calls


@pytest.fixture
def generated_spec(monkeypatch):
    """Stub out the (LLM + validation) tail of the OpenAPI generation."""
    async def fake_generate(markdown, user_goal):
        return {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1.0.0"},
            "paths": {},
        }

    monkeypatch.setattr(openapi_generator,
                        "generate_openapi_spec_from_markdown", fake_generate)
    monkeypatch.setattr(openapi_generator, "clean_openapi_spec",
                        lambda spec: spec)
    monkeypatch.setattr(openapi_generator, "validate_openapi_spec",
                        lambda spec: (True, None))


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def isolated_workdir(tmp_path, monkeypatch):
    """Run each test in its own working directory so .skyffel is isolated."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_failed_page_is_not_cached_or_merged(fake_extraction, generated_spec,
                                             read_cache, tmp_path):
    documents = {
        URL_OK_1: _page("Intro", "Intro body"),
        URL_BAD: "<html><body><p>no main here</p></body></html>",
        URL_OK_2: _page("Search", "Search body"),
    }
    report = PageReport(documents.keys())

    result = _run(openapi_generator.generate_openapi_spec(
        documents, "my goal", page_report=report))

    # Successful pages made it into the spec, the failing one did not.
    assert result["paths"] == {}
    assert report.get(URL_OK_1).used_for_generation is True
    assert report.get(URL_OK_2).used_for_generation is True
    assert report.get(URL_BAD).used_for_generation is False
    assert report.get(URL_BAD).failed_stage == "preprocess"
    assert "No main content" in report.get(URL_BAD).error

    cache = read_cache(tmp_path / ".skyffel" / "selector_cache.json")
    assert md5_of(URL_OK_1) in cache
    assert md5_of(URL_OK_2) in cache
    assert md5_of(URL_BAD) not in cache

    combined = (tmp_path / ".skyffel" /
                "relevant_html_combined.html").read_text(encoding="utf-8")
    assert "Intro body" in combined
    assert "Search body" in combined

    markdown = (tmp_path / ".skyffel" / "markdown.md").read_text(
        encoding="utf-8")
    assert "Intro body" in markdown and "Search body" in markdown


def test_cache_hit_skips_selector_election(fake_extraction, generated_spec,
                                           tmp_path):
    import json

    skyffel = tmp_path / ".skyffel"
    skyffel.mkdir()
    cache_path = skyffel / "selector_cache.json"
    cache_path.write_text(json.dumps({
        md5_of(URL_OK_1): {
            "main_section_selector": MAIN_SELECTOR,
            "irrelevant_sections_selectors": IRRELEVANT_SELECTORS,
            "url": URL_OK_1,
        }
    }), encoding="utf-8")

    report = PageReport([URL_OK_1])
    _run(openapi_generator.generate_openapi_spec(
        {URL_OK_1: _page("Intro", "Intro body")}, "goal", page_report=report))

    # No LLM selector election happened for the cached page.
    assert fake_extraction["main"] == []
    assert fake_extraction["irrelevant"] == []
    assert report.get(URL_OK_1).cache_hit is True

    # The cached value is preserved verbatim.
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache[md5_of(URL_OK_1)]["main_section_selector"] == MAIN_SELECTOR


def test_stale_cached_selector_is_reelected_and_does_not_lose_the_page(
        fake_extraction, generated_spec, tmp_path):
    import json

    skyffel = tmp_path / ".skyffel"
    skyffel.mkdir()
    cache_path = skyffel / "selector_cache.json"
    cache_path.write_text(json.dumps({
        md5_of(URL_OK_1): {
            "main_section_selector": "#gone",
            "irrelevant_sections_selectors": [],
            "url": URL_OK_1,
        }
    }), encoding="utf-8")

    report = PageReport([URL_OK_1])
    _run(openapi_generator.generate_openapi_spec(
        {URL_OK_1: _page("Intro", "Intro body")}, "goal", page_report=report))

    assert report.get(URL_OK_1).used_for_generation is True
    assert report.get(URL_OK_1).cache_hit is False
    # The stale entry was refreshed with the newly elected selectors.
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache[md5_of(URL_OK_1)]["main_section_selector"] == MAIN_SELECTOR


def test_failed_page_does_not_overwrite_existing_cache(fake_extraction,
                                                       generated_spec, tmp_path):
    import json

    skyffel = tmp_path / ".skyffel"
    skyffel.mkdir()
    cache_path = skyffel / "selector_cache.json"
    stale_entry = {
        "main_section_selector": "#old",
        "irrelevant_sections_selectors": [],
        "url": URL_BAD,
    }
    cache_path.write_text(json.dumps({md5_of(URL_BAD): stale_entry}),
                          encoding="utf-8")

    # Election keeps failing, so the page never preprocesses successfully.
    def always_failing(raw_html, main_section_selector=None,
                       irrelevant_sections_selectors=None):
        raise ValueError("No main content found with selector `#nope`.")

    import airbyte_connector_generator_poc.openapi_generator as gen
    original = gen.extract_relevant_html
    gen.extract_relevant_html = always_failing
    try:
        report = PageReport([URL_BAD])
        with pytest.raises(NoUsablePagesError):
            _run(gen.generate_openapi_spec(
                {URL_BAD: "<html><body><p>nothing</p></body></html>"}, "goal",
                page_report=report))
    finally:
        gen.extract_relevant_html = original

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache[md5_of(URL_BAD)] == stale_entry
    assert report.get(URL_BAD).failed_stage == "preprocess"


def test_all_preprocessing_failures_raise(generated_spec, monkeypatch, tmp_path):
    def always_failing(raw_html, main_section_selector=None,
                       irrelevant_sections_selectors=None):
        raise ValueError("No main content found with selector `#nope`.")

    monkeypatch.setattr(openapi_generator, "extract_relevant_html",
                        always_failing)

    report = PageReport([URL_OK_1, URL_OK_2])
    with pytest.raises(NoUsablePagesError) as excinfo:
        _run(openapi_generator.generate_openapi_spec(
            {URL_OK_1: _page("Intro", "Intro body"),
             URL_OK_2: _page("Search", "Search body")},
            "goal", page_report=report))

    assert excinfo.value.report is report
    assert len(report.failed_pages) == 2
    assert not (tmp_path / ".skyffel" / "markdown.md").exists()
