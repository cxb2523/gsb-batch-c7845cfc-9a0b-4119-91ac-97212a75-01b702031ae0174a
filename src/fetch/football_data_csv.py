"""Download per-season CSVs from football-data.co.uk for a league.

Usage: python -m src.fetch.football_data_csv [--league KEY] [--force]
Files are written to data/raw/football_data/{league}/{season}.csv.

Each season is settled independently: a failure on one season is recorded and
the next season is still fetched. Content is validated and written to a
temporary file first, then atomically swapped onto the target path, so failed
downloads never corrupt or truncate an existing cache file. ``fetch_all``
prints a per-season summary and the process exits non-zero if any season
failed.
"""
from __future__ import annotations

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

SOURCE = "football-data.co.uk"


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


def _validate_csv(text: str, expected_div: str) -> str:
    """Validate a downloaded CSV and return the division code found.

    Convention: the first column of the first DATA row must be the division
    code configured for the league. Raises ValueError on empty payloads,
    missing data rows or a mismatch.
    """
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        raise ValueError("response contained no CSV rows")
    if len(lines) < 2:
        raise ValueError("response contained a header but no data rows")
    div = lines[1].split(",")[0].lstrip("\ufeff")  # strip a leading BOM if present
    if div != expected_div:
        raise ValueError(
            f"unexpected Div '{div}' (expected {expected_div}) — check URL"
        )
    return div


def fetch_season_result(
    season: str, league: str = config.DEFAULT_LEAGUE, force: bool = False
) -> SeasonResult:
    """Fetch one season and always return a structured result (never raises)."""
    spec = config.LEAGUES[league]
    out_dir = config.league_dir("football_data", league)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{season}.csv"
    is_in_progress = season == spec.get("in_progress")

    if _is_cached(out_path, spec, season, force):
        print(f"[skip] {league} {season} (cached)")
        return SeasonResult(
            season, SeasonStatus.SKIPPED, str(out_path), "cached"
        )
    if is_in_progress and not force:
        print(f"[refresh] {league} {season} (in progress)")

    url = config.FOOTBALL_DATA_URL.format(
        code=config.football_data_code(season), div=spec["fd_div"]
    )
    try:
        text = get_text(url)
        div = _validate_csv(text, spec["fd_div"])
        # Validation passed: only now touch the real file, atomically.
        atomic_write_text(out_path, text)
    except FileNotFoundError:
        print(f"[404]  {league} {season} — not published yet")
        return SeasonResult(season, SeasonStatus.NOT_FOUND, None, "404")
    except Exception as e:  # noqa: BLE001 — one season must not kill the run
        print(f"[fail] {league} {season}: {e}")
        return SeasonResult(season, SeasonStatus.FAILED, None, str(e))

    print(f"[ok]   {league} {season} (Div={div}) -> {out_path.relative_to(config.WORKSPACE)}")
    return SeasonResult(season, SeasonStatus.SUCCESS, str(out_path), f"Div={div}")


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
