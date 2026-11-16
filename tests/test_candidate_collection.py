"""Tests para la recolección multi-fuente y el filtrado previo a la verificación.

Todos los tests usan HTML construido localmente y respuestas falsas:
NO se hace ninguna petición de red real y los resultados no implican
que ningún dominio esté verificado en producción.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import BLOCKED_DOMAINS
from core.candidates import CollectionResult, SourceResult
from core.domain_tester import DomainTester


class FakeResp:
    """Respuesta HTTP falsa para sustituir curl_cffi en tests."""
    def __init__(self, status_code=200, text="", url="https://example.test/"):
        self.status_code = status_code
        self.text = text
        self.url = url

    def json(self):
        import json
        return json.loads(self.text)


# ── Fusión de fuentes ────────────────────────────────────────────────────────

def test_same_domain_from_multiple_sources_is_merged():
    """Un dominio visto en varias fuentes se prueba una vez y acumula fuentes."""
    collection = CollectionResult(sources=[
        SourceResult(name="open-slum", domains=["annas-archive.gl", "z-lib.gd"]),
        SourceResult(name="reddit", domains=["annas-archive.gl"]),
        SourceResult(name="mastodon", domains=["annas-archive.gl", "libgen.la"]),
    ])
    candidates = collection.candidates
    assert candidates["annas-archive.gl"] == ["mastodon", "open-slum", "reddit"]
    assert candidates["z-lib.gd"] == ["open-slum"]
    # deduplicado: aparece una sola vez en la lista que va a verificación
    assert collection.domains().count("annas-archive.gl") == 1
    assert sorted(collection.domains()) == ["annas-archive.gl", "libgen.la", "z-lib.gd"]


def test_failed_source_does_not_drop_other_sources():
    """Una fuente fallida se registra pero los demás candidatos sobreviven."""
    collection = CollectionResult(sources=[
        SourceResult(name="open-slum", ok=False, error="HTTP 503"),
        SourceResult(name="reddit", domains=["annas-archive.pk"]),
    ])
    assert collection.failed_sources == ["open-slum"]
    assert collection.domains() == ["annas-archive.pk"]


def test_social_crawler_isolates_crashing_source(monkeypatch):
    """Si una fuente explota con excepción, crawl_all sigue con las demás."""
    import core.social_crawler as sc

    def boom():
        raise ConnectionError("simulated network failure")

    def fine():
        return SourceResult(name="nitter", domains=["annas-archive.gd"])

    monkeypatch.setattr(sc, "_SOURCES", [("reddit", boom), ("nitter", fine)])
    results = sc.SocialCrawler().crawl_all()

    by_name = {r.name: r for r in results}
    assert by_name["reddit"].ok is False
    assert "ConnectionError" in by_name["reddit"].error
    assert by_name["nitter"].ok is True

    collection = CollectionResult(sources=results)
    assert collection.failed_sources == ["reddit"]
    assert collection.domains() == ["annas-archive.gd"]


def test_source_marked_failed_when_all_requests_fail(monkeypatch):
    """Reddit: todas las peticiones fallan -> fuente fallida, sin candidatos."""
    import core.social_crawler as sc

    def always_fail(*args, **kwargs):
        raise ConnectionError("simulated outage")

    monkeypatch.setattr(sc.requests, "get", always_fail)
    result = sc._crawl_reddit()
    assert result.ok is False
    assert result.domains == []
    assert result.error


def test_open_slum_partial_failure_still_ok(monkeypatch):
    """open-slum: una URL cae y otra responde -> la fuente sigue ok."""
    import core.open_slum_crawler as osc

    html = "<html>Anna's Archive GL y Z-Library GD además de libgen.la</html>"
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("simulated timeout")
        return FakeResp(200, html, url)

    monkeypatch.setattr(osc, "OPEN_SLUM_URLS", ["https://a.test", "https://b.test"])
    monkeypatch.setattr(osc.requests, "get", fake_get)
    result = osc.crawl_open_slum()
    assert result.ok is True
    assert "annas-archive.gl" in result.domains
    assert "z-lib.gd" in result.domains


def test_open_slum_total_failure_marks_source_failed(monkeypatch):
    """open-slum: todas las URLs devuelven != 200 -> fuente fallida."""
    import core.open_slum_crawler as osc

    monkeypatch.setattr(osc, "OPEN_SLUM_URLS", ["https://a.test", "https://b.test"])
    monkeypatch.setattr(osc.requests, "get",
                        lambda url, **kw: FakeResp(503, "", url))
    result = osc.crawl_open_slum()
    assert result.ok is False
    assert result.domains == []
    assert "503" in result.error


# ── Verificación: classify descarta, no entra en activos ─────────────────────

def test_classify_discarded_domain_not_in_active(monkeypatch):
    """Un dominio que responde 200 pero classify no reconoce queda descartado."""
    import core.domain_tester as dt

    def fake_get(url, **kwargs):
        return FakeResp(200, "<html><title>Random SEO Shop</title></html>", url)

    monkeypatch.setattr(dt.requests, "get", fake_get)
    tester = DomainTester()
    active = tester.test_multiple(["random-shop.gl"])
    assert active == []
    assert tester.by_service == {}
    assert tester.discarded == ["random-shop.gl"]


def test_recognized_domain_enters_by_service(monkeypatch):
    """Control: un host reconocible sí entra en activos y en by_service."""
    import core.domain_tester as dt

    def fake_get(url, **kwargs):
        return FakeResp(200, "<html></html>", url)

    monkeypatch.setattr(dt.requests, "get", fake_get)
    tester = DomainTester()
    active = tester.test_multiple(["annas-archive.gl"])
    assert active == ["https://annas-archive.gl"]
    assert tester.by_service["annas_archive"] == ["https://annas-archive.gl"]
    assert tester.discarded == []


# ── BLOCKED_DOMAINS se excluye antes de testear ──────────────────────────────

def test_blocked_domains_removed_before_testing():
    """Los dominios de BLOCKED_DOMAINS no llegan nunca a la fase de prueba."""
    blocked_sample = BLOCKED_DOMAINS[0]
    collection = CollectionResult(sources=[
        SourceResult(name="open-slum", domains=[blocked_sample, "annas-archive.gl"]),
        SourceResult(name="reddit", domains=[blocked_sample]),
    ])
    filtered, found = collection.filter_blocked(BLOCKED_DOMAINS)
    assert found == [blocked_sample]
    assert blocked_sample not in filtered
    assert list(filtered) == ["annas-archive.gl"]


def test_blocked_domain_never_reaches_tester(monkeypatch):
    """Flujo run.py: el dominio bloqueado no genera ni una petición."""
    import core.domain_tester as dt

    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        return FakeResp(200, "<html></html>", url)

    monkeypatch.setattr(dt.requests, "get", fake_get)
    blocked_sample = BLOCKED_DOMAINS[0]
    collection = CollectionResult(sources=[
        SourceResult(name="open-slum", domains=[blocked_sample, "annas-archive.gl"]),
    ])
    candidates, _ = collection.filter_blocked(BLOCKED_DOMAINS)
    tester = DomainTester()
    tester.test_multiple(list(candidates))
    assert all(blocked_sample not in url for url in requested)
    assert requested == ["https://annas-archive.gl"]
