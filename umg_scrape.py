# -*- coding: utf-8 -*-
"""UMG leaderboard scraping pipelines (XP board and earnings board).

Both leaderboards share the same fetch/parse/persist flow implemented here;
only the target URL, the ORM model and the numeric column differ:

* XP board       -> ``db.player_sql.Player``            (``xp`` is an int)
* earnings board -> ``db.top_earning_players_sql.Earning_Player`` (``earnings`` is a float)

Everything is injectable so the pipelines can be tested without real network
or a real Postgres:

* pass ``html=...`` to parse a locally built HTML fragment, or
* pass ``fetcher=callable`` to control what the "request" returns, and
* pass any SQLAlchemy session (real, SQLite-backed, or a test double) as
  ``session`` -- persistence only uses ``query/filter/delete/add/commit/
  rollback``.

Row-level vs page-level failures
--------------------------------
* Page-level failures (request error, or no ``<table id="leaderboard-table">``
  in the page) make :func:`scrape_leaderboard` return ``ScrapeResult(ok=False,
  error=...)`` *without touching the database*. Nothing is raised.
* Row-level failures (a row with missing columns or an illegal numeric value)
  only skip that row: it is recorded in ``ScrapeResult.failed`` with a
  human-readable reason and every other row is still parsed and persisted.

Duplicate usernames
-------------------
The two original scripts handled re-scraped usernames inconsistently
(``xp_scrape.py`` deleted the old row before inserting, ``earnings_scrape.py``
only deleted when some row with ``id > 0`` existed). The unified policy here
is: **an existing row with the same username is replaced by the new value, so
the table never contains duplicate usernames.** This is implemented as
"delete all rows with that username, then insert the new one" (see
:func:`replace_player`). Delete-then-insert was chosen over update-in-place
because it also collapses pre-existing duplicates and works identically
through any session-like test double; the trade-off is a new primary-key
value and a refreshed ``date`` on every re-scrape, which matches how these
scripts were already being used (full leaderboard refresh).
"""

from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

XP_URL = "https://www.umggaming.com/leaderboards"
EARNINGS_URL = "https://umggaming.com/leaderboards/earnings"

LEADERBOARD_TABLE_ID = "leaderboard-table"


class LeaderboardNotFoundError(Exception):
    """The page does not contain the expected leaderboard table."""


class RowParseError(Exception):
    """A single leaderboard row could not be parsed; only that row is skipped."""


@dataclass
class RowFailure:
    """One row that could not be processed, with a human-readable reason."""

    row: int  # 1-based position of the <tr> within <tbody>
    reason: str

    def __str__(self):
        return f"row {self.row}: {self.reason}"


@dataclass
class ScrapeResult:
    """Outcome of one scrape run.

    ``ok`` is False only for page-level failures (request failed or the
    leaderboard table was not found); in that case ``error`` explains why and
    no database writes were attempted. When ``ok`` is True, ``inserted`` rows
    were persisted and ``failed`` lists the skipped rows with reasons.
    """

    ok: bool
    url: str
    error: str = None
    inserted: int = 0
    failed: list = field(default_factory=list)  # list[RowFailure]

    @property
    def failed_count(self):
        return len(self.failed)


@dataclass
class BoardSpec:
    """What differs between the two leaderboards."""

    model: type  # ORM model class (Player or Earning_Player)
    value_column: str  # keyword arg of the model: "xp" or "earnings"
    parse_value: object  # callable str -> int/float, raises ValueError on bad input
    label: str  # column header used when printing


def parse_xp(text):
    """XP is stored as an integer; tolerate thousands separators."""
    return int(text.replace(",", ""))


def parse_earnings(text):
    """Earnings are stored as a float; drop the leading '$' and separators."""
    return float(text.strip().lstrip("$").replace(",", ""))


def xp_spec():
    from db.player_sql import Player

    return BoardSpec(model=Player, value_column="xp", parse_value=parse_xp, label="XP")


def earnings_spec():
    from db.top_earning_players_sql import Earning_Player

    return BoardSpec(
        model=Earning_Player,
        value_column="earnings",
        parse_value=parse_earnings,
        label="Earnings",
    )


def default_fetcher(url):
    """Fetch ``url`` with requests; raises on transport or HTTP errors."""
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def find_leaderboard_rows(html):
    """Return the <tr> elements of the leaderboard, or raise LeaderboardNotFoundError."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": LEADERBOARD_TABLE_ID})
    if table is None:
        raise LeaderboardNotFoundError(
            f'no table with id "{LEADERBOARD_TABLE_ID}" found in page'
        )
    tbody = table.find("tbody")
    if tbody is None:
        raise LeaderboardNotFoundError(
            f'leaderboard table (id "{LEADERBOARD_TABLE_ID}") has no <tbody>'
        )
    return tbody.find_all("tr")


def parse_row(tr, spec):
    """Parse one <tr> into (place, username, value); raises RowParseError."""
    tds = tr.find_all("td")
    if len(tds) < 5:
        raise RowParseError(f"expected at least 5 columns, found {len(tds)}")
    place = tds[0].get_text(strip=True)
    links = tds[1].find_all("a")
    if len(links) < 2:
        raise RowParseError("username cell does not contain the profile link")
    username = links[1].get_text(strip=True)
    if not username:
        raise RowParseError("username is empty")
    raw_value = tds[4].get_text(strip=True)
    try:
        value = spec.parse_value(raw_value)
    except ValueError:
        raise RowParseError(
            f"invalid {spec.label} value {raw_value!r}"
        ) from None
    return place, username, value


def replace_player(session, model, **fields):
    """Replace any existing row(s) with the same username by the new values.

    Unified duplicate policy (see module docstring): delete every row with
    this username, then insert the new row and commit, so the table never
    holds duplicate usernames. Each row is committed independently so one
    failing row never rolls back rows that already succeeded.
    """
    session.query(model).filter(model.username == fields["username"]).delete(
        synchronize_session=False
    )
    session.add(model(**fields))
    session.commit()


def scrape_leaderboard(session, url, spec, fetcher=None, html=None, printer=print):
    """Run one leaderboard scrape: fetch -> parse -> persist row by row.

    Never raises for expected failures; returns a ScrapeResult instead.
    """
    if html is not None:
        page = html
    else:
        try:
            page = (fetcher or default_fetcher)(url)
        except Exception as exc:
            return ScrapeResult(ok=False, url=url, error=f"request failed: {exc}")

    try:
        rows = find_leaderboard_rows(page)
    except LeaderboardNotFoundError as exc:
        return ScrapeResult(ok=False, url=url, error=str(exc))

    result = ScrapeResult(ok=True, url=url)
    printer("Place", "Username", spec.label, sep="  ")
    for row_number, tr in enumerate(rows, start=1):
        try:
            place, username, value = parse_row(tr, spec)
        except RowParseError as exc:
            result.failed.append(RowFailure(row_number, str(exc)))
            continue
        printer(place, username, value, sep="  ")
        try:
            replace_player(
                session, spec.model, username=username, place=place,
                **{spec.value_column: value},
            )
        except Exception as exc:
            session.rollback()
            result.failed.append(RowFailure(row_number, f"database write failed: {exc}"))
            continue
        result.inserted += 1
    return result


def scrape_xp(session, fetcher=None, html=None, printer=print):
    """Scrape the XP leaderboard into the ``players`` table."""
    return scrape_leaderboard(session, XP_URL, xp_spec(), fetcher, html, printer)


def scrape_earnings(session, fetcher=None, html=None, printer=print):
    """Scrape the earnings leaderboard into the ``paid_players`` table."""
    return scrape_leaderboard(session, EARNINGS_URL, earnings_spec(), fetcher, html, printer)
