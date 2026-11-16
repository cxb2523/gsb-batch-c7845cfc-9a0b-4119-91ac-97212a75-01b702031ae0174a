# core/candidates.py
"""
Estructuras para candidatos con trazabilidad de fuente.

Cada fuente (open-slum, reddit, nitter, telegram, mastodon, ...) devuelve un
SourceResult con los dominios que descubrió y su estado (ok / fallo + motivo).
CollectionResult fusiona los resultados de todas las fuentes: un mismo dominio
descubierto por varias fuentes se prueba UNA sola vez y conserva la lista de
fuentes que lo aportaron.
"""

from dataclasses import dataclass, field


@dataclass
class SourceResult:
    """Resultado de rastrear UNA fuente.

    ok=False significa que la fuente nunca llegó a responder con éxito
    (error de red, HTTP != 200 en todos los intentos, o respuesta
    imposible de parsear). Un fallo así no debe tumbar a las demás fuentes.
    """
    name: str
    domains: list = field(default_factory=list)
    ok: bool = True
    error: str = ""

    def status_line(self) -> str:
        if self.ok:
            return f"{self.name}: ok, {len(self.domains)} candidatos"
        return f"{self.name}: FALLO ({self.error})"


@dataclass
class CollectionResult:
    """Fusión de los SourceResult de todas las fuentes de una ronda."""

    sources: list = field(default_factory=list)

    @property
    def failed_sources(self) -> list:
        """Nombres de fuentes que nunca respondieron con éxito esta ronda."""
        return [s.name for s in self.sources if not s.ok]

    @property
    def candidates(self) -> dict:
        """dominio -> lista ordenada de fuentes que lo descubrieron.

        El mismo dominio aportado por varias fuentes aparece una sola vez
        (se fusionan las fuentes, no se duplica el candidato).
        """
        merged = {}
        for src in self.sources:
            for domain in src.domains:
                key = domain.strip().lower()
                if not key:
                    continue
                merged.setdefault(key, [])
                if src.name not in merged[key]:
                    merged[key].append(src.name)
        return {d: sorted(names) for d, names in merged.items()}

    def domains(self) -> list:
        """Lista deduplicada de dominios candidatos (sin probar)."""
        return list(self.candidates)

    def add_extra(self, domains, source_name: str) -> None:
        """Añade dominios de una fuente no rastreada (config, DB, ...)."""
        self.sources.append(SourceResult(name=source_name, domains=list(domains)))

    def filter_blocked(self, blocked) -> tuple:
        """Excluye dominios bloqueados ANTES de testear.

        Returns:
            (candidates_filtrados, bloqueados_encontrados):
            candidates_filtrados es un dict dominio -> fuentes sin los
            dominios presentes en `blocked`; bloqueados_encontrados es la
            lista ordenada de dominios eliminados.
        """
        blocked = set(blocked)
        found = sorted(d for d in self.candidates if d in blocked)
        filtered = {d: s for d, s in self.candidates.items() if d not in blocked}
        return filtered, found
