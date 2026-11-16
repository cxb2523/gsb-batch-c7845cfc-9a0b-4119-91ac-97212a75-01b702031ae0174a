"""
Módulo para rastrear open-slum.org y extraer dominios candidatos.

Devuelve un SourceResult ("open-slum"): si TODAS las URLs fallan (error de
red o HTTP != 200) la fuente queda marcada como fallida, pero el resto de
fuentes de la ronda no se ven afectadas.
"""

import re
from curl_cffi import requests
from config.settings import OPEN_SLUM_URLS, BROWSER_IMPERSONATION
from core.candidates import SourceResult

# Patrones nombre → plantilla de dominio
_PATTERNS = [
    (r"Anna's Archive ([A-Z]{2,3})", "annas-archive.{code}"),
    (r"Libgen\+([A-Z]{2,3})",        "libgen.{code}"),
    (r"Libgen\+([A-Z]{2,3})",        "librarygenesis.{code}"),
    (r"Z-Library ([A-Z]{2,3})",      "z-lib.{code}"),
    (r"Z-Library ([A-Z]{2,3})",      "zlibrary.{code}"),
    (r'z-lib ([a-z]{2,3})',          "z-lib.{code}"),
    (r'1lib ([a-z]{2,3})',           "1lib.{code}"),
]

# Dominios con TLD característicos — sin grupo de captura para obtener dominio completo
_TLD_PATTERN = r'[a-z0-9\-]+\.(?:gl|gd|pk|vg|sk|la|bz)'


def _extract_from_text(text):
    """Extrae candidatos de una página de open-slum ya descargada."""
    candidates = []

    # Patrones nombre → dominio construido
    for pattern, template in _PATTERNS:
        for match in re.findall(pattern, text):
            candidates.append(template.format(code=match.lower()))

    # Dominios .sk directos
    for match in re.findall(r'([a-z0-9\-]+\.sk)', text):
        candidates.append(match.lower())

    # Cualquier dominio con TLD de archivo (sin grupo de captura → devuelve dominio completo)
    for match in re.findall(_TLD_PATTERN, text, re.IGNORECASE):
        candidates.append(match.lower())

    return candidates


def crawl_open_slum():
    """
    Visita open-slum.org y extrae dominios candidatos.

    Returns:
        SourceResult: nombre "open-slum", dominios deduplicados (sin probar)
        y ok=False solo si ninguna URL respondió con éxito.
    """
    result = SourceResult(name="open-slum")
    candidates = []
    errors = []
    successes = 0

    for url in OPEN_SLUM_URLS:
        print(f"[*] Rastreando: {url}")
        try:
            r = requests.get(url, impersonate=BROWSER_IMPERSONATION, timeout=15)
            if r.status_code != 200:
                print(f"  [!] HTTP {r.status_code}")
                errors.append(f"{url} -> HTTP {r.status_code}")
                continue

            successes += 1
            candidates.extend(_extract_from_text(r.text))

        except Exception as e:
            print(f"  [!] Error: {e}")
            errors.append(f"{url} -> {type(e).__name__}: {str(e)[:80]}")

    if OPEN_SLUM_URLS and successes == 0:
        result.ok = False
        result.error = "; ".join(errors)[:200] or "sin URLs configuradas"

    result.domains = sorted(set(c.strip() for c in candidates if c and len(c) > 5))
    print(f"[+] open-slum: {len(result.domains)} candidatos")
    if result.domains:
        print(f"[*] Muestra: {result.domains[:10]}")

    return result
