"""Fetch Understat league data and save the raw JSON.

Understat loads its data via an AJAX endpoint (the league HTML page no longer
embeds `var data = {...}`). The endpoint is:

    https://understat.com/getLeagueData/{slug}/{start_year}

which returns JSON with keys: teams, players, dates. `dates` is a list of
match dicts: {id, isResult, h:{title}, a:{title}, goals:{h,a}, xG:{h,a},
datetime:"YYYY-MM-DD HH:MM:SS", forecast}.

Files are written to data/raw/understat/{league}/{year}.json.

Usage: python -m src.fetch.understat [--league KEY] [--force]

Like the football-data fetcher, each season is settled independently: one
season failing never stops the rest, downloads are validated and written via
a temporary file then atomically swapped into place, and the process exits
non-zero if any season failed.
"""
from __future__ import annotations

import json
import sys

import config
from src.fetch.http import get_text, polite_sleep
from src.fetch.io import (
    FetchSummary,
    SeasonResult,
    SeasonStatus,
    atomic_write_text,
    print_summary,
)

SOURCE = "understat"


def _data_url(league: str) -> str:
    slug = config.LEAGUES[league]["understat_slug"]
    return f"https://understat.com/getLeagueData/{slug}/{{year}}"


def _xhr_headers(league: str) -> dict:
    # The AJAX endpoint rejects plain requests (404) unless these are present.
    slug = config.LEAGUES[league]["understat_slug"]
    return {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://understat.com/league/{slug}",
    }


def extract_matches(data: dict) -> list[dict]:
    """Return the list of match dicts from an Understat payload.

    Handles both the current `dates` list format and the legacy `matches`
    format for robustness.
    """
    if isinstance(data.get("dates"), list):
        return data["dates"]
    if isinstance(data.get("matches"), list):
        return data["matches"]
    return []


def _is_cached(out_path, spec: dict, season: str, force: bool) -> bool:
    """Cache rule: completed seasons with a non-empty file are skipped.

    The in-progress season is the only file that gains new matches (the
    source updates it in place), so it is always re-downloaded even when
    force=False; completed seasons stay cached. force=True re-downloads all.
    """
    is_in_progress = season == spec.get("in_progress")
    if force or is_in_progress:
        return False
    return out_path.exists() and out_path.stat().st_size > 0


def parse_payload(text: str) -> tuple[dict, list[dict]]:
    """Parse and validate a downloaded Understat payload.

    The text must parse as a JSON object from which matches can be extracted
    (a non-empty list under `dates` or the legacy `matches` key). Raises
    ValueError / json.JSONDecodeError otherwise.
    """
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("payload is not a JSON object")
    matches = extract_matches(data)
    if not isinstance(matches, list) or not matches:
        raise ValueError("payload contains no extractable matches")
    return data, matches


def fetch_season_result(
    season: str, league: str = config.DEFAULT_LEAGUE, force: bool = False
) -> SeasonResult:
    """Fetch one season and always return a structured result (never raises)."""
    spec = config.LEAGUES[league]
    out_dir = config.league_dir("understat", league)
    out_dir.mkdir(parents=True, exist_ok=True)
    year = config.understat_year(season)
    out_path = out_dir / f"{year}.json"
    is_in_progress = season == spec.get("in_progress")

    if _is_cached(out_path, spec, season, force):
        print(f"[skip] {league} {season} (cached)")
        return SeasonResult(
            season, SeasonStatus.SKIPPED, str(out_path), "cached"
        )
    if is_in_progress and not force:
        print(f"[refresh] {league} {season} (in progress)")

    url = _data_url(league).format(year=year)
    try:
        text = get_text(url, extra_headers=_xhr_headers(league))
        data, matches = parse_payload(text)
        # Validation passed: only now touch the real file, atomically.
        atomic_write_text(out_path, json.dumps(data, ensure_ascii=False))
    except FileNotFoundError:
        print(f"[404]  {league} {season} — not published yet")
        return SeasonResult(season, SeasonStatus.NOT_FOUND, None, "404")
    except Exception as e:  # noqa: BLE001 — one season must not kill the run
        print(f"[fail] {league} {season}: {e}")
        return SeasonResult(season, SeasonStatus.FAILED, None, str(e))

    n = len(matches)
    print(f"[ok]   {league} {season} ({n} matches) -> {out_path.relative_to(config.WORKSPACE)}")
    return SeasonResult(season, SeasonStatus.SUCCESS, str(out_path), f"{n} matches")


def fetch_season(
    season: str, league: str = config.DEFAULT_LEAGUE, force: bool = False
) -> str | None:
    """Backward-compatible single-season wrapper.

    Returns the output path on success/cache hit, or None when the season is
    not published (404) or the fetch failed; raises nothing.
    """
    result = fetch_season_result(season, league=league, force=force)
    if result.status in (SeasonStatus.SUCCESS, SeasonStatus.SKIPPED):
        return result.path
    return None


def fetch_all(
    league: str = config.DEFAULT_LEAGUE, force: bool = False
) -> FetchSummary:
    """Fetch every configured season, settling each one independently.

    A failure on one season never stops the remaining seasons. The returned
    summary records success / skipped / 404 / failed per season and is also
    printed as a readable list with totals.
    """
    summary = FetchSummary(source=SOURCE, league=league)
    for i, season in enumerate(config.LEAGUES[league]["seasons"]):
        if i > 0:
            polite_sleep()
        summary.results.append(
            fetch_season_result(season, league=league, force=force)
        )
    print_summary(summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    force = "--force" in argv
    league = config.DEFAULT_LEAGUE
    if "--league" in argv:
        league = argv[argv.index("--league") + 1]
    summary = fetch_all(league=league, force=force)
    if summary.failed:
        print(
            f"Exiting with errors: {len(summary.failed)} season(s) failed: "
            + ", ".join(r.season for r in summary.failed),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
