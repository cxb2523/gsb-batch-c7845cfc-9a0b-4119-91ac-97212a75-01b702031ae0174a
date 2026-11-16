from dataclasses import dataclass
from enum import Enum


class LeagueSectionState(str, Enum):
    """Outcome of fetching and parsing a single league section (results/fixtures)."""

    OK = "ok"
    """Section was fetched and parsed, and at least one of its matches survived event_url de-duplication."""

    EMPTY = "empty"
    """Section was fetched and parsed without errors, but none of its matches appear in the final `matches` list."""

    FAILED = "failed"
    """The section page could not be fetched (e.g. a timeout waiting for matches)."""


@dataclass
class LeagueSectionStatus:
    """Per-section status for a league scrape.

    `matches_count` counts only matches that this section contributed to the final
    de-duplicated `matches` list, so matches dropped as duplicates are never counted.
    """

    section: str
    url: str
    status: LeagueSectionState
    matches_count: int = 0
