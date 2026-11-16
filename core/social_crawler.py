# core/social_crawler.py
"""
Rastrea fuentes sociales para descubrir nuevos dominios de Anna's Archive.

Fuentes implementadas (cada una devuelve un SourceResult propio):
  - reddit:   Reddit (JSON API pública, sin autenticación)
  - nitter:   X/Twitter vía instancias Nitter públicas
  - telegram: Telegram canales públicos (vía t.me/s/)
  - mastodon: Mastodon (API pública)

Una fuente se marca ok=False cuando NINGUNA de sus peticiones tuvo éxito
(error de red, HTTP != 200 o respuesta imposible de parsear). El fallo de
una fuente nunca descarta los resultados de las demás.
"""

import re
import time
from curl_cffi import requests

from config.settings import (
    BROWSER_IMPERSONATION,
    REDDIT_SUBREDDITS, REDDIT_QUERIES,
    NITTER_INSTANCES, NITTER_QUERIES,
    TELEGRAM_CHANNELS, TELEGRAM_SPAM_KEYWORDS,
    MASTODON_INSTANCES, MASTODON_QUERIES,
)
from core.candidates import SourceResult
from core.domain_extractor import extract_domains

_HEADERS_JSON = {
    "User-Agent": "anna-archive-hub/1.0 (domain tracker; educational)"
}


def _finish(result, candidates, attempts, successes, errors):
    """Rellena un SourceResult a partir de los contadores de la ronda."""
    result.domains = sorted(set(c.lower() for c in candidates if c))
    if attempts and successes == 0:
        result.ok = False
        result.error = "; ".join(errors)[-200:] or "todas las peticiones fallaron"
    print(f"  [{result.name}] {result.status_line()}")
    return result


def _crawl_reddit() -> SourceResult:
    """Busca en subreddits usando la API JSON pública de Reddit."""
    result = SourceResult(name="reddit")
    candidates, errors = [], []
    attempts = successes = 0

    for subreddit in REDDIT_SUBREDDITS:
        for query in REDDIT_QUERIES:
            url = (
                f"https://old.reddit.com/r/{subreddit}/search.json"
                f"?q={query}&sort=new&limit=25&restrict_sr=1"
            )
            attempts += 1
            try:
                r = requests.get(url, headers=_HEADERS_JSON, timeout=10)
                if r.status_code != 200:
                    errors.append(f"{subreddit}/{query}: HTTP {r.status_code}")
                    continue

                posts = r.json().get("data", {}).get("children", [])
                successes += 1
                for post in posts:
                    data = post.get("data", {})
                    text = f"{data.get('title', '')} {data.get('selftext', '')}"
                    candidates.extend(extract_domains(text))

                time.sleep(1)  # respetar rate limit de Reddit

            except (ValueError, KeyError) as e:
                errors.append(f"{subreddit}/{query}: respuesta malformada: {e}")
            except Exception as e:
                errors.append(f"{subreddit}/{query}: {type(e).__name__}: {str(e)[:80]}")

    return _finish(result, candidates, attempts, successes, errors)


def _crawl_nitter() -> SourceResult:
    """
    Busca en X/Twitter usando instancias públicas de Nitter.
    Prueba cada instancia en orden y usa la primera que responde.
    """
    result = SourceResult(name="nitter")
    candidates, errors = [], []
    attempts = successes = 0

    for query in NITTER_QUERIES:
        query_encoded = query.replace(" ", "+")
        for instance in NITTER_INSTANCES:
            url = f"{instance}/search?q={query_encoded}&f=tweets"
            attempts += 1
            try:
                r = requests.get(
                    url,
                    impersonate=BROWSER_IMPERSONATION,
                    timeout=12,
                    allow_redirects=True,
                )
                if r.status_code != 200:
                    errors.append(f"{instance}: HTTP {r.status_code}")
                    continue

                successes += 1
                tweet_texts = re.findall(
                    r'class="tweet-content[^"]*"[^>]*>(.*?)</div>',
                    r.text,
                    re.DOTALL
                )
                for raw in tweet_texts:
                    clean = re.sub(r'<[^>]+>', ' ', raw)
                    candidates.extend(extract_domains(clean))

                break  # instancia funcionó, pasar a siguiente query

            except Exception as e:
                errors.append(f"{instance}: {type(e).__name__}: {str(e)[:80]}")
                continue

    return _finish(result, candidates, attempts, successes, errors)


def _is_telegram_spam(text: str) -> bool:
    """Descarta mensajes de Telegram que contienen indicadores de spam."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in TELEGRAM_SPAM_KEYWORDS)


def _crawl_telegram() -> SourceResult:
    """
    Rastrea canales públicos de Telegram vía t.me/s/{canal}.
    Solo funciona con canales públicos.
    TELEGRAM_CHANNELS está vacío por defecto — añade canales verificados manualmente.
    """
    result = SourceResult(name="telegram")
    if not TELEGRAM_CHANNELS:
        print("  [telegram] Sin canales configurados (vacío por defecto).")
        return result

    candidates, errors = [], []
    attempts = successes = 0

    for channel in TELEGRAM_CHANNELS:
        url = f"https://t.me/s/{channel}"
        attempts += 1
        try:
            r = requests.get(
                url,
                impersonate=BROWSER_IMPERSONATION,
                timeout=12,
            )
            if r.status_code != 200:
                errors.append(f"@{channel}: HTTP {r.status_code}")
                continue

            successes += 1
            messages = re.findall(
                r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                r.text,
                re.DOTALL
            )
            for raw in messages:
                clean = re.sub(r'<[^>]+>', ' ', raw)
                if _is_telegram_spam(clean):
                    continue
                candidates.extend(extract_domains(clean))

        except Exception as e:
            errors.append(f"@{channel}: {type(e).__name__}: {str(e)[:80]}")

    return _finish(result, candidates, attempts, successes, errors)


def _crawl_mastodon() -> SourceResult:
    """
    Busca en instancias de Mastodon usando su API pública de búsqueda.
    No requiere autenticación para búsquedas públicas básicas.
    """
    result = SourceResult(name="mastodon")
    candidates, errors = [], []
    attempts = successes = 0

    for instance in MASTODON_INSTANCES:
        for query in MASTODON_QUERIES:
            url = f"{instance}/api/v2/search"
            params = {"q": query, "type": "statuses", "limit": 40}
            attempts += 1
            try:
                r = requests.get(url, params=params, timeout=10)
                if r.status_code != 200:
                    errors.append(f"{instance}: HTTP {r.status_code}")
                    continue

                statuses = r.json().get("statuses", [])
                successes += 1
                for status in statuses:
                    raw = status.get("content", "")
                    clean = re.sub(r'<[^>]+>', ' ', raw)
                    candidates.extend(extract_domains(clean))

            except (ValueError, KeyError) as e:
                errors.append(f"{instance}: respuesta malformada: {e}")
            except Exception as e:
                errors.append(f"{instance}: {type(e).__name__}: {str(e)[:80]}")

    return _finish(result, candidates, attempts, successes, errors)


# (nombre de fuente, función que la rastrea) — el nombre es la etiqueta que
# aparecerá junto a cada candidato en los logs y en el reporte de la ronda.
_SOURCES = [
    ("reddit", _crawl_reddit),
    ("nitter", _crawl_nitter),
    ("telegram", _crawl_telegram),
    ("mastodon", _crawl_mastodon),
]


class SocialCrawler:
    """Orquesta todas las fuentes sociales."""

    def crawl_all(self) -> list:
        """
        Rastrea todas las fuentes configuradas.

        Returns:
            list[SourceResult]: un resultado por fuente. Si una fuente
            explota con una excepción no controlada, se registra como
            fallida y las demás continúan — nunca se pierden sus resultados.
        """
        results = []

        for name, crawl_fn in _SOURCES:
            print(f"[*] Rastreando fuente: {name}...")
            try:
                results.append(crawl_fn())
            except Exception as e:
                # Última línea de defensa: una fuente rota no tumba a las demás.
                results.append(SourceResult(
                    name=name, ok=False,
                    error=f"excepción no controlada: {type(e).__name__}: {str(e)[:120]}",
                ))
                print(f"  [{name}] FALLO inesperado: {type(e).__name__}: {str(e)[:80]}")

        return results
