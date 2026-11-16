# -*- coding: utf-8 -*-
"""Tests for the UMG leaderboard scraping pipelines (umg_scrape.py).

No real network and no real Postgres required:

* HTML fragments are built locally and injected via ``html=`` / ``fetcher=``.
* ``FakeSession`` is a test double verifying parsing, per-row settlement and
  the exact write calls made against the session.
* A real SQLAlchemy session over in-memory SQLite verifies actual inserts and
  the duplicate-username replacement policy end to end.
* If a reachable Postgres is configured (env ``DB`` or the README default),
  one extra test persists through a real session against it; otherwise it is
  skipped.
"""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from umg_scrape import (
    EARNINGS_URL,
    XP_URL,
    RowFailure,
    earnings_spec,
    find_leaderboard_rows,
    parse_earnings,
    parse_xp,
    scrape_earnings,
    scrape_leaderboard,
    scrape_xp,
    xp_spec,
)
from db.player_sql import Player
from db.top_earning_players_sql import Earning_Player


# --------------------------------------------------------------------------
# Sample HTML fragments
# --------------------------------------------------------------------------

def _row(place, username, value, value_class=""):
    return (
        "<tr>"
        f"<td>{place}</td>"
        f'<td><a href="/u/{username}"><img/></a><a href="/u/{username}">{username}</a></td>'
        "<td>team</td><td>region</td>"
        f"<td{value_class}>{value}</td>"
        "</tr>"
    )


def _page(*rows):
    return (
        "<html><body>"
        '<table id="leaderboard-table"><thead></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></body></html>"
    )


XP_PAGE = _page(
    _row("1st", "alice", "12,345"),
    _row("2nd", "bob", "9876"),
)

EARNINGS_PAGE = _page(
    _row("1st", "alice", "$1,234.50"),
    _row("2nd", "bob", "$250"),
)

# row 2 has too few columns, row 4 has an illegal number, row 5 lacks the
# profile link; rows 1 and 3 are valid.
BROKEN_XP_PAGE = _page(
    _row("1st", "alice", "100"),
    "<tr><td>2nd</td><td>broken</td></tr>",
    _row("3rd", "carol", "300"),
    _row("4th", "dave", "not-a-number"),
    "<tr><td>5th</td><td><a href='/u/eve'></a></td><td>x</td><td>y</td><td>500</td></tr>",
)

NO_TABLE_PAGE = "<html><body><p>nothing here</p></body></html>"


# --------------------------------------------------------------------------
# Test-double session
# --------------------------------------------------------------------------

class FakeQuery:
    def __init__(self, session, model):
        self.session = session
        self.model = model

    def filter(self, *criteria):
        self.session.filtered.append((self.model, criteria))
        return self

    def delete(self, synchronize_session=False):
        self.session.deleted.append(self.model)
        return 1


class FakeSession:
    """Records the session calls the pipeline makes, without any database."""

    def __init__(self, fail_on_commit_for=()):
        self.added = []
        self.deleted = []
        self.filtered = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on_commit_for = set(fail_on_commit_for)

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1
        if self.added and self.added[-1].username in self.fail_on_commit_for:
            raise RuntimeError("simulated database error")

    def rollback(self):
        self.rollbacks += 1


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def test_parse_xp_accepts_thousands_separator():
    assert parse_xp("12,345") == 12345


def test_parse_earnings_strips_dollar_and_commas():
    assert parse_earnings("$1,234.50") == 1234.50


def test_find_leaderboard_rows_requires_table():
    with pytest.raises(Exception, match="leaderboard-table"):
        find_leaderboard_rows(NO_TABLE_PAGE)


# --------------------------------------------------------------------------
# Full pipeline against the fake session
# --------------------------------------------------------------------------

def test_xp_scrape_success_inserts_every_row():
    session = FakeSession()
    result = scrape_xp(session, html=XP_PAGE, printer=lambda *a, **k: None)

    assert result.ok and result.error is None
    assert result.url == XP_URL
    assert result.inserted == 2 and result.failed == []
    # per-row settlement: one commit per row, no rollbacks
    assert session.commits == 2 and session.rollbacks == 0
    # duplicate policy: each row deletes the old username row before insert
    assert session.deleted == [Player, Player]
    added = {p.username: p for p in session.added}
    assert added["alice"].xp == 12345 and isinstance(added["alice"].xp, int)
    assert added["alice"].place == "1st"
    assert added["bob"].xp == 9876


def test_earnings_scrape_success_parses_floats():
    session = FakeSession()
    result = scrape_earnings(session, html=EARNINGS_PAGE, printer=lambda *a, **k: None)

    assert result.ok and result.inserted == 2
    assert result.url == EARNINGS_URL
    added = {p.username: p for p in session.added}
    assert added["alice"].earnings == 1234.50
    assert isinstance(added["alice"].earnings, float)
    assert added["bob"].earnings == 250.0


def test_broken_rows_are_skipped_with_reasons_and_good_rows_persist():
    session = FakeSession()
    result = scrape_xp(session, html=BROKEN_XP_PAGE, printer=lambda *a, **k: None)

    assert result.ok  # page-level success despite bad rows
    assert result.inserted == 2
    assert [p.username for p in session.added] == ["alice", "carol"]
    assert session.commits == 2  # only good rows committed

    assert result.failed_count == 3
    by_row = {f.row: f.reason for f in result.failed}
    assert "5 columns" in by_row[2]
    assert "invalid XP value" in by_row[4]
    assert "profile link" in by_row[5]
    # reasons are human readable
    for failure in result.failed:
        assert isinstance(str(failure), str) and failure.reason


def test_request_failure_returns_failed_result_without_db_writes():
    def boom(url):
        raise ConnectionError("network down")

    session = FakeSession()
    result = scrape_xp(session, fetcher=boom, printer=lambda *a, **k: None)

    assert not result.ok
    assert "network down" in result.error
    assert session.added == [] and session.commits == 0


def test_missing_leaderboard_table_returns_failed_result_without_db_writes():
    session = FakeSession()
    result = scrape_earnings(session, html=NO_TABLE_PAGE, printer=lambda *a, **k: None)

    assert not result.ok
    assert "leaderboard-table" in result.error
    assert session.added == [] and session.commits == 0


def test_db_error_on_one_row_only_skips_that_row():
    session = FakeSession(fail_on_commit_for={"alice"})
    result = scrape_xp(session, html=XP_PAGE, printer=lambda *a, **k: None)

    assert result.ok
    assert result.inserted == 1
    assert result.failed_count == 1
    assert "database write failed" in result.failed[0].reason
    assert session.rollbacks == 1
    assert [p.username for p in session.added] == ["alice", "bob"]


def test_scrape_leaderboard_accepts_injected_fetcher():
    calls = []

    def fetcher(url):
        calls.append(url)
        return XP_PAGE

    result = scrape_xp(FakeSession(), fetcher=fetcher, printer=lambda *a, **k: None)
    assert result.ok and result.inserted == 2
    assert calls == [XP_URL]


# --------------------------------------------------------------------------
# Real SQLAlchemy session over in-memory SQLite (no Postgres needed)
# --------------------------------------------------------------------------

@pytest.fixture
def sqlite_session():
    engine = create_engine("sqlite:///:memory:")
    Player.metadata.create_all(engine)
    Earning_Player.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_xp_rows_actually_persisted(sqlite_session):
    result = scrape_xp(sqlite_session, html=XP_PAGE, printer=lambda *a, **k: None)
    assert result.ok and result.inserted == 2

    rows = sqlite_session.query(Player).order_by(Player.username).all()
    assert [(p.username, p.place, p.xp) for p in rows] == [
        ("alice", "1st", 12345),
        ("bob", "2nd", 9876),
    ]


def test_earnings_rows_actually_persisted(sqlite_session):
    result = scrape_earnings(sqlite_session, html=EARNINGS_PAGE, printer=lambda *a, **k: None)
    assert result.ok and result.inserted == 2

    rows = sqlite_session.query(Earning_Player).order_by(Earning_Player.username).all()
    assert [(p.username, p.earnings) for p in rows] == [
        ("alice", 1234.50),
        ("bob", 250.0),
    ]


def test_same_username_is_replaced_never_duplicated(sqlite_session):
    # pre-existing row for alice, plus a leftover duplicate from older runs
    sqlite_session.add(Player(username="alice", place="9th", xp=1))
    sqlite_session.add(Player(username="alice", place="8th", xp=2))
    sqlite_session.commit()

    result = scrape_xp(sqlite_session, html=XP_PAGE, printer=lambda *a, **k: None)
    assert result.ok

    alices = sqlite_session.query(Player).filter(Player.username == "alice").all()
    assert len(alices) == 1
    assert alices[0].xp == 12345 and alices[0].place == "1st"


def test_earnings_same_username_replaced(sqlite_session):
    sqlite_session.add(Earning_Player(username="bob", place="7th", earnings=1.0))
    sqlite_session.commit()

    result = scrape_earnings(sqlite_session, html=EARNINGS_PAGE, printer=lambda *a, **k: None)
    assert result.ok

    bobs = sqlite_session.query(Earning_Player).filter(Earning_Player.username == "bob").all()
    assert len(bobs) == 1
    assert bobs[0].earnings == 250.0


# --------------------------------------------------------------------------
# Optional: real Postgres, only when reachable (README default or $DB)
# --------------------------------------------------------------------------

def _postgres_session_or_none():
    url = os.getenv("DB", "postgresql+psycopg2://test:testpassword@localhost:5432/xp_db")
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        connection = engine.connect()
    except Exception:
        return None
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()
    Player.metadata.create_all(connection)
    Earning_Player.metadata.create_all(connection)
    return session, connection, transaction


@pytest.fixture
def postgres_session():
    ctx = _postgres_session_or_none()
    if ctx is None:
        pytest.skip("no reachable Postgres (set DB env var to enable)")
    session, connection, transaction = ctx
    yield session
    session.close()
    transaction.rollback()
    connection.close()


def test_scrape_against_real_postgres(postgres_session):
    result = scrape_xp(postgres_session, html=XP_PAGE, printer=lambda *a, **k: None)
    assert result.ok and result.inserted == 2
    rows = postgres_session.query(Player).filter(Player.username.in_(["alice", "bob"])).all()
    assert {p.username for p in rows} == {"alice", "bob"}

    result = scrape_earnings(postgres_session, html=EARNINGS_PAGE, printer=lambda *a, **k: None)
    assert result.ok and result.inserted == 2
    rows = postgres_session.query(Earning_Player).filter(
        Earning_Player.username.in_(["alice", "bob"])
    ).all()
    assert {p.username for p in rows} == {"alice", "bob"}
