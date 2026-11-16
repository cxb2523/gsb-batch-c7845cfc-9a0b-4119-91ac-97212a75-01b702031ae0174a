"""Shared fetch plumbing: per-season result types, atomic writes, summaries.

Both fetch entry points (football-data.co.uk CSV and Understat JSON) settle
one season at a time. A season ends in exactly one of four states:

* ``SUCCESS``   - downloaded, validated and atomically written
* ``SKIPPED``   - a non-empty cached file already existed (cache rule)
* ``NOT_FOUND`` - the source answered 404 (season not published yet)
* ``FAILED``    - any transport/parse/validation error; later seasons still run

Downloads always go to a temporary file in the target directory and only
replace the real file once validation has passed, so a failed or interrupted
download can never leave a half-written cache file behind.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class SeasonStatus(str, Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    NOT_FOUND = "not_found"
    FAILED = "failed"


# Human-readable labels used in the per-season lines and the summary.
STATUS_LABELS = {
    SeasonStatus.SUCCESS: "ok   ",
    SeasonStatus.SKIPPED: "skip ",
    SeasonStatus.NOT_FOUND: "404  ",
    SeasonStatus.FAILED: "fail ",
}


@dataclass
class SeasonResult:
    """Outcome of fetching a single season."""

    season: str
    status: SeasonStatus
    path: str | None = None
    detail: str = ""  # error message for FAILED, short note otherwise


@dataclass
class FetchSummary:
    """Aggregated outcome of one ``fetch_all`` run."""

    source: str
    league: str
    results: list[SeasonResult] = field(default_factory=list)

    @property
    def failed(self) -> list[SeasonResult]:
        return [r for r in self.results if r.status is SeasonStatus.FAILED]

    def count(self, status: SeasonStatus) -> int:
        return sum(1 for r in self.results if r.status is status)


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically.

    The content is first written to a uniquely named temporary file in the
    same directory, flushed and closed, and then ``os.replace``d onto the
    target (atomic on the same filesystem on both POSIX and Windows). Any
    existing target file is left untouched unless the replacement itself
    succeeds; the temporary file is removed on every error path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def print_summary(summary: FetchSummary) -> None:
    """Print a readable per-season list followed by totals."""
    print(f"\n{summary.source} fetch summary — {summary.league}:")
    for r in summary.results:
        label = STATUS_LABELS[r.status]
        line = f"  [{label}] {r.season}"
        if r.status is SeasonStatus.FAILED and r.detail:
            line += f" — {r.detail}"
        elif r.detail:
            line += f" ({r.detail})"
        print(line)
    print(
        "  total {total}: {ok} ok, {skip} skipped (cached), "
        "{nf} not published (404), {fail} failed".format(
            total=len(summary.results),
            ok=summary.count(SeasonStatus.SUCCESS),
            skip=summary.count(SeasonStatus.SKIPPED),
            nf=summary.count(SeasonStatus.NOT_FOUND),
            fail=summary.count(SeasonStatus.FAILED),
        )
    )
    if summary.failed:
        print(
            "  FAILED seasons: "
            + ", ".join(r.season for r in summary.failed)
        )
