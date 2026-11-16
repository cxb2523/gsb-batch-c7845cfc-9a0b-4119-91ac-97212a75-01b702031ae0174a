"""Fetching from an OpenAlgo server: the chunk window table, chunk planning, the walk
over a date range, pacing and HTTP 429 backoff (FR-DATA-07). No test waits: the
clock and sleep are injected.

The window values asserted here are the ones of PRD 06 section 4 and of the
reference note on the OpenAlgo broker adapters, written out by hand.
"""

import datetime as dt

import pytest
from data_fixtures.fakes import (
    FakeCalendar,
    FakeClient,
    FakeClock,
    at_ist,
    broker_shape,
    days_between,
    historify_shape,
    make_provider,
    server_load,
    truth_rows,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from openalgo.backtest.data.brokers import BROKER_PROFILES, DEFAULT_PROFILE, broker_profile
from openalgo.backtest.data.fetching import (
    STOP_BROKER_START,
    STOP_RATE_LIMITED,
    STOP_SERVER_ERROR,
    STOP_TRANSPORT,
    is_retryable_status,
    plan_chunks,
    rows_to_records,
)
from openalgo.backtest.errors import (
    ConfigError,
    DataError,
    NoDataError,
    ServerError,
    SymbolNotFoundError,
    TimestampError,
)

START = dt.date(2024, 1, 1)
END = dt.date(2024, 5, 31)  # 152 days after START, inclusive
DAYS = days_between(START, END)
NOW = at_ist(dt.date(2024, 6, 3), 18, 0)
CAL = FakeCalendar()
CONFIRMED_BROKERS = ("zerodha", "angel", "upstox", "fyers", "dhan", "kotak")
XTS_FAMILY = ("compositedge", "fivepaisaxts", "ibulls", "iifl", "jainamxts", "rmoney", "wisdom")
BUSY = {"status": "error", "message": "OpenAlgo is pacing requests for your broker"}
BARS_PER_DAY_5M = 75


def _client(broker="zerodha", days=DAYS, **options):
    rows = broker_shape(truth_rows(CAL, "NSE", days, step=300))
    return FakeClient(broker=broker, api={("X", "NSE", "5m"): rows}, **options)


def _load(client, start=START, end=END, source="api", now_ns=NOW, **options):
    return server_load(
        client, "5m", start, end, calendar=CAL, now_ns=now_ns, source=source, cache=False, **options
    )


def _windows(client, source="api"):
    return [(first, last) for _, _, first, last in client.history_calls(source)]


# --- the per-broker window table ----------------------------------------------------


@pytest.mark.parametrize(
    "broker, interval, days",
    [
        ("zerodha", "1m", 60), ("zerodha", "1h", 60), ("zerodha", "D", 2000),
        ("angel", "1m", 30), ("angel", "3m", 60), ("angel", "5m", 100), ("angel", "10m", 100),
        ("angel", "15m", 200), ("angel", "30m", 200), ("angel", "1h", 400), ("angel", "D", 2000),
        ("upstox", "1m", 30), ("upstox", "15m", 30), ("upstox", "30m", 90), ("upstox", "4h", 90),
        ("upstox", "D", 3650),
        ("fyers", "5s", 25), ("fyers", "1m", 60), ("fyers", "1h", 60), ("fyers", "D", 300),
        ("dhan", "1m", 90), ("dhan", "15m", 90),
        ("kotak", "1m", 30), ("kotak", "5m", 30), ("kotak", "10m", 60), ("kotak", "15m", 60),
        ("kotak", "30m", 90), ("kotak", "1h", 90), ("kotak", "D", 180),
    ],
)  # fmt: skip
def test_chunk_window_of_a_confirmed_broker(broker, interval, days):
    assert broker_profile(broker).window_days(interval) == days


@pytest.mark.parametrize(
    "broker, interval, days", [("angel", "2m", 30), ("upstox", "20m", 30), ("kotak", "2h", 90)]
)
def test_interval_the_table_does_not_list_gets_the_next_finer_window(broker, interval, days):
    assert broker_profile(broker).window_days(interval) == days


@pytest.mark.parametrize("interval, days", [("5s", 25), ("1m", 30), ("1h", 30), ("D", 180)])
def test_unknown_broker_gets_the_conservative_default_window(interval, days):
    assert broker_profile("somebroker").window_days(interval) == days
    assert broker_profile(None).window_days(interval) == days


@pytest.mark.parametrize("interval", ["5s", "1m", "5m", "15m", "1h", "D"])
def test_no_confirmed_broker_has_a_smaller_window_than_the_default(interval):
    smallest = min(BROKER_PROFILES[name].window_days(interval) for name in CONFIRMED_BROKERS)
    assert DEFAULT_PROFILE.window_days(interval) <= smallest


def test_exactly_the_six_prd_brokers_are_marked_verified():
    verified = {name for name, profile in BROKER_PROFILES.items() if profile.verified}
    assert verified == set(CONFIRMED_BROKERS)


@pytest.mark.parametrize("broker", XTS_FAMILY)
def test_xts_family_timestamps_are_flagged_unverified_with_no_shift_assumed(broker):
    profile = BROKER_PROFILES[broker]
    assert profile.timestamps_verified is False
    assert profile.intraday_shift_seconds == 0
    assert "XTS family" in profile.notes


def test_broker_name_is_matched_without_regard_to_case():
    assert broker_profile("Zerodha").name == "zerodha"


# --- chunk planning -----------------------------------------------------------------


def test_chunks_are_anchored_at_the_end_so_only_the_oldest_is_short():
    assert plan_chunks(START, END, 60) == [
        (dt.date(2024, 1, 1), dt.date(2024, 2, 1)),  # the remainder: 32 days
        (dt.date(2024, 2, 2), dt.date(2024, 4, 1)),  # 60 days
        (dt.date(2024, 4, 2), dt.date(2024, 5, 31)),  # 60 days
    ]


@pytest.mark.parametrize(
    "end, window_days, expected_chunks",
    [
        (dt.date(2024, 1, 1), 30, 1),   # one day
        (dt.date(2024, 2, 29), 60, 1),  # exactly one window (60 days)
        (dt.date(2024, 3, 1), 60, 2),   # one day more than a window
    ],
)  # fmt: skip
def test_chunk_count_at_the_window_boundary(end, window_days, expected_chunks):
    assert len(plan_chunks(START, end, window_days)) == expected_chunks


@pytest.mark.parametrize("start, end, window_days", [(END, START, 30), (START, END, 0)])
def test_impossible_chunk_plan_is_a_config_error(start, end, window_days):
    with pytest.raises(ConfigError):
        plan_chunks(start, end, window_days)


@settings(max_examples=100, deadline=None)
@given(span=st.integers(0, 4000), window=st.integers(1, 400))
def test_chunks_cover_every_day_exactly_once_within_the_window(span, window):
    start = dt.date(2015, 1, 1)
    end = start + dt.timedelta(days=span)
    chunks = plan_chunks(start, end, window)
    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    assert all(low <= high and (high - low).days < window for low, high in chunks)
    one_day = dt.timedelta(days=1)
    assert all(nxt[0] == prev[1] + one_day for prev, nxt in zip(chunks, chunks[1:], strict=False))


# --- the walk over a date range -----------------------------------------------------


@pytest.mark.parametrize(
    "broker, expected",
    [
        (
            "zerodha",  # 60 days per request
            [("2024-04-02", "2024-05-31"), ("2024-02-02", "2024-04-01"),
             ("2024-01-01", "2024-02-01")],
        ),
        (
            "kotak",  # 30 days per request at 5m
            [("2024-05-02", "2024-05-31"), ("2024-04-02", "2024-05-01"),
             ("2024-03-03", "2024-04-01"), ("2024-02-02", "2024-03-02"),
             ("2024-01-03", "2024-02-01"), ("2024-01-01", "2024-01-02")],
        ),
    ],
)  # fmt: skip
def test_broker_range_is_requested_in_table_windows_newest_first(broker, expected):
    client = _client(broker)
    _load(client)
    assert _windows(client) == expected


def test_chunk_days_overrides_the_table():
    client = _client(days=days_between(START, dt.date(2024, 1, 31)))
    _load(client, end=dt.date(2024, 1, 31), provider_options={"chunk_days": 10})
    assert len(_windows(client)) == 4  # 31 days in windows of 10


def test_historify_range_is_read_in_engine_windows_oldest_first():
    rows = historify_shape(truth_rows(CAL, "NSE", days_between(START, dt.date(2024, 3, 31))))
    client = FakeClient(db={("X", "NSE", "1m"): rows})
    server_load(
        client,
        "1m",
        START,
        dt.date(2024, 3, 31),
        calendar=CAL,
        now_ns=NOW,
        source="db",
        cache=False,
    )
    assert _windows(client, "db") == [
        ("2024-01-01", "2024-01-31"),  # the remainder: 31 days
        ("2024-02-01", "2024-03-31"),  # 60 days, the engine window for 1m
    ]


def test_broker_walk_stops_at_the_first_empty_window_that_had_a_session():
    held = [day for day in DAYS if day >= dt.date(2024, 5, 10)]
    client = _client(days=held)
    result = _load(client)
    # zerodha windows: Apr 2 to May 31 has bars, Feb 2 to Apr 1 is empty, the oldest is not asked
    assert _windows(client) == [("2024-04-02", "2024-05-31"), ("2024-02-02", "2024-04-01")]
    assert result.meta.fetch.stopped == (
        "the broker returned no bars for 2024-02-02 to 2024-04-01, so the history is taken "
        "to start after 2024-04-01; nothing older was asked for"
    )


def test_empty_window_between_cached_sessions_does_not_end_the_broker_walk(tmp_path):
    # The broker has no bars for Wednesday 2024-01-10, a session by the calendar (as a
    # holiday the calendar does not know), and the cache holds the sessions around it.
    wednesday, friday = dt.date(2024, 1, 10), dt.date(2024, 1, 19)
    client = _client(days=[day for day in days_between(START, friday) if day != wednesday])
    cached = {"source": "api", "cache_dir": tmp_path}
    for first, last in [(dt.date(2024, 1, 8), dt.date(2024, 1, 9)), (dt.date(2024, 1, 11), friday)]:
        server_load(client, "5m", first, last, calendar=CAL, now_ns=NOW, **cached)
    client.calls.clear()
    result = server_load(client, "5m", START, friday, calendar=CAL, now_ns=NOW, **cached)
    assert _windows(client) == [("2024-01-10", "2024-01-10"), ("2024-01-01", "2024-01-05")]
    assert result.meta.fetch.stopped is None
    assert result.meta.short_history is False
    assert len(result.bars) == 14 * BARS_PER_DAY_5M  # 15 weekdays, Wednesday 10th without bars


@pytest.mark.parametrize("chunk_days", [1, 2, 3])
def test_empty_weekend_window_does_not_end_the_broker_walk(chunk_days):
    three_weeks = days_between(START, dt.date(2024, 1, 19))
    result = _load(
        _client(days=three_weeks), end=dt.date(2024, 1, 19),
        provider_options={"chunk_days": chunk_days},
    )  # fmt: skip
    assert len(result.bars) == 15 * BARS_PER_DAY_5M
    assert result.meta.short_history is False


def test_window_of_todays_unfinished_session_does_not_end_the_broker_walk():
    today = dt.date(2024, 1, 5)
    before_the_first_bar_closes = at_ist(today, 9, 16)
    result = _load(
        _client(days=days_between(START, dt.date(2024, 1, 4))), end=today,
        now_ns=before_the_first_bar_closes, provider_options={"chunk_days": 1},
    )  # fmt: skip
    assert len(result.bars) == 4 * BARS_PER_DAY_5M


@pytest.mark.parametrize(
    "status, body",
    [
        (401, {"status": "error", "message": "Session expired"}),
        (403, {"status": "error", "message": "forbidden"}),
    ],
)
def test_a_final_error_of_an_older_window_ends_the_broker_walk_with_the_newest_windows_kept(
    status, body
):
    # A non-retryable 4xx is not an answer a retry could change: the walk
    # converges at once (no retry is spent) and the newest window's bars are
    # returned with the reason recorded, instead of one error discarding the fetch.
    client = _client()
    newest = broker_shape(truth_rows(CAL, "NSE", [END], step=300))
    client.queue(200, {"status": "success", "data": newest})
    client.queue(status, body)
    result = _load(client, broker="zerodha")
    fetch = result.meta.fetch
    assert fetch.windows_fetched == 1
    assert fetch.stop_reason == "server_error"
    assert "final error" in fetch.stopped and f"HTTP {status}" in fetch.stopped
    assert (fetch.requests, fetch.retries) == (2, 0)
    assert len(result.bars) == BARS_PER_DAY_5M


def test_a_5xx_that_persists_is_retried_before_the_walk_converges():
    # A 5xx is retryable: unlike a 4xx it spends the backoff budget first.
    client = _client()
    newest = broker_shape(truth_rows(CAL, "NSE", [END], step=300))
    client.queue(200, {"status": "success", "data": newest})
    client.queue(502, None, persistent=True)
    result = _load(client, broker="zerodha", provider_options={"max_retries": 2})
    fetch = result.meta.fetch
    assert fetch.windows_fetched == 1
    assert fetch.stop_reason == "server_error"
    assert "HTTP 502" in fetch.stopped
    assert (fetch.requests, fetch.retries) == (4, 2)


def test_historify_still_raises_on_a_failed_window():
    # The db source keeps its old contract: read oldest first, a failed window
    # raises rather than converging, so the failure cannot be mistaken for the
    # end of stored history.
    rows = historify_shape(truth_rows(CAL, "NSE", DAYS[:5], step=300))
    client = FakeClient(db={("X", "NSE", "5m"): rows})
    client.queue(500, None, persistent=True)
    with pytest.raises(ServerError, match="HTTP 500"):
        _load(client, source="db", end=dt.date(2024, 1, 5))


def test_not_json_404_from_the_store_is_reported_as_what_it_is():
    client = FakeClient()
    client.queue(404, None)
    with pytest.raises(ServerError, match="HTTP 404: the body is not JSON"):
        _load(client, source="db")


# --- the broker name ----------------------------------------------------------------


def test_broker_is_asked_from_the_server_once_per_provider():
    client = _client("angel", days=DAYS[:10])
    provider = make_provider(client, CAL, NOW, source="api")
    for _ in range(2):
        provider.bars("X", "NSE", "5m", START, dt.date(2024, 1, 10))
    assert [endpoint for endpoint, _ in client.calls].count("ping") == 1


def test_broker_given_explicitly_is_not_asked_from_the_server():
    client = _client("angel", days=DAYS[:10])
    result = _load(client, end=dt.date(2024, 1, 10), broker="Zerodha")
    assert result.meta.broker == "zerodha"
    assert client.endpoints() == {"history"}


def test_unidentified_broker_uses_the_default_window_and_says_so():
    client = _client(broker=None, days=days_between(START, dt.date(2024, 2, 29)))
    result = _load(client, end=dt.date(2024, 2, 29))
    assert len(_windows(client)) == 2  # 60 days in default windows of 30
    assert result.meta.broker is None
    assert "the broker could not be identified" in result.meta.warnings[0]


def test_historify_fetch_does_not_ask_for_the_broker():
    rows = historify_shape(truth_rows(CAL, "NSE", DAYS[:5], step=300))
    client = FakeClient(db={("X", "NSE", "5m"): rows})
    _load(client, end=dt.date(2024, 1, 5), source="db")
    assert client.endpoints() == {"history"}


# --- pacing -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "broker, pacing, least_gap_seconds",
    [("kotak", None, 1.0), ("zerodha", None, 0.35), ("angel", None, 0.5), ("kotak", 2.5, 2.5)],
    ids=["kotak table", "zerodha table", "angel table", "pacing argument"],
)
def test_history_requests_are_never_closer_together_than_the_pacing(
    broker, pacing, least_gap_seconds
):
    clock = FakeClock()
    client = _client(broker, clock=clock.clock)
    _load(client, clock=clock, broker=broker, provider_options={"pacing": pacing})
    gaps = [b - a for a, b in zip(client.call_times, client.call_times[1:], strict=False)]
    assert gaps  # the range needs more than one request for every broker here
    assert min(gaps) == pytest.approx(least_gap_seconds)


def test_waiting_is_done_through_the_injected_sleep():
    clock = FakeClock()
    _load(_client("kotak", clock=clock.clock), clock=clock, broker="kotak")
    assert clock.sleeps == [1.0] * 5  # six requests, one second apart


@pytest.mark.parametrize("option", [{"pacing": -1}, {"chunk_days": 0}])
def test_negative_pacing_and_empty_window_are_config_errors(option):
    with pytest.raises(ConfigError):
        _load(_client(), provider_options=option)


# --- HTTP 429 -----------------------------------------------------------------------


def test_http_429_is_retried_with_doubling_waits():
    clock = FakeClock()
    client = _client(days=DAYS[:10])
    for _ in range(3):  # three rate-limited answers, then the in-memory rows
        client.queue(429, BUSY)
    result = _load(client, end=dt.date(2024, 1, 10), clock=clock, broker="zerodha")
    assert clock.sleeps == [1.0, 2.0, 4.0]
    assert (result.meta.fetch.requests, result.meta.fetch.retries) == (4, 3)
    assert len(result.bars) == BARS_PER_DAY_5M * 8


@pytest.mark.parametrize(
    "retry_after, waited", [("7", 7.0), ("600", 20.0), ("Wed, 21 Oct 2026 07:28:00 GMT", 1.0)],
    ids=["longer than the backoff", "capped at backoff_max", "a date is ignored"],
)  # fmt: skip
def test_retry_after_header_lengthens_the_wait_up_to_the_cap(retry_after, waited):
    clock = FakeClock()
    client = _client(days=DAYS[:10])
    client.queue(429, BUSY, {"Retry-After": retry_after})
    _load(
        client, end=dt.date(2024, 1, 10), clock=clock, broker="zerodha",
        provider_options={"backoff_max": 20.0},
    )  # fmt: skip
    assert clock.sleeps == [waited]


def test_backoff_gives_up_after_max_retries_and_converges_with_no_bars_lost():
    # The retries are exhausted with the existing backoff schedule (1 s, 2 s),
    # the walk ends there instead of raising, and the report says why: every
    # request and retry is still counted. No window was answered, so the load is
    # a NoDataError whose message carries the same stop reason.
    clock = FakeClock()
    client = _client(days=DAYS[:10])
    client.queue(429, BUSY, persistent=True)
    with pytest.raises(NoDataError, match="rate limited .*HTTP 429"):
        _load(
            client, end=dt.date(2024, 1, 10), clock=clock, broker="zerodha",
            provider_options={"max_retries": 2},
        )  # fmt: skip
    assert clock.sleeps == [1.0, 2.0]
    asked = client.history_calls("api")
    assert len(asked) == 3 and all(call == asked[0] for call in asked)  # the one window, retried


def test_rate_limit_after_some_windows_keeps_the_windows_already_answered():
    clock = FakeClock()
    client = _client()
    newest = broker_shape(truth_rows(CAL, "NSE", [END], step=300))
    client.queue(200, {"status": "success", "data": newest})
    client.queue(429, BUSY, persistent=True)
    result = _load(
        client, clock=clock, broker="zerodha", provider_options={"max_retries": 2},
    )  # fmt: skip
    fetch = result.meta.fetch
    assert fetch.windows_fetched == 1
    assert fetch.stop_reason == "rate_limited"
    assert "rate limited (HTTP 429)" in fetch.stopped
    assert "2024-02-02 to 2024-04-01" in fetch.stopped
    assert (fetch.requests, fetch.retries) == (4, 2)
    assert [sleep for sleep in clock.sleeps if sleep >= 1.0] == [1.0, 2.0]  # backoff only
    assert fetch.answered == [("2024-04-02", "2024-05-31")]
    assert len(result.bars) == BARS_PER_DAY_5M  # only the newest window's rows
    assert result.meta.short_history is True
    assert "The fetch stopped going back" in result.meta.warnings[0]


# --- transport failures (no HTTP response) ------------------------------------------


def _transport_client(fail_before):
    """A broker whose newest window is reachable; older ones cannot be reached."""
    rows = broker_shape(truth_rows(CAL, "NSE", DAYS, step=300))
    return FakeClient(api={("X", "NSE", "5m"): rows}, transport_fail_before=fail_before)


def test_transport_failure_is_retried_then_converges_keeping_the_newest_windows():
    clock = FakeClock()
    client = _transport_client(dt.date(2024, 4, 2))
    result = _load(
        client, clock=clock, broker="zerodha", provider_options={"max_retries": 2},
    )  # fmt: skip
    fetch = result.meta.fetch
    assert fetch.windows_fetched == 1
    assert fetch.stop_reason == "transport_failure"
    assert "could not be reached" in fetch.stopped and "2024-02-02 to 2024-04-01" in fetch.stopped
    # One initial attempt plus two retries of the same window (each failed attempt
    # is a request), backed off 1 s then 2 s.
    assert (fetch.requests, fetch.retries) == (4, 2)
    assert [sleep for sleep in clock.sleeps if sleep >= 1.0] == [1.0, 2.0]
    assert len(result.bars) == BARS_PER_DAY_5M * 44  # Apr 2 to May 31, weekdays
    assert result.meta.short_history is True


def test_transport_failure_on_the_first_window_converges_to_a_no_data_error_with_the_reason():
    clock = FakeClock()
    client = _transport_client(END + dt.timedelta(days=1))  # every window is unreachable
    with pytest.raises(NoDataError, match="could not be reached"):
        _load(client, clock=clock, broker="zerodha", provider_options={"max_retries": 1})
    assert [sleep for sleep in clock.sleeps if sleep >= 1.0] == [1.0]


def test_historify_does_not_converge_on_a_transport_failure():
    rows = historify_shape(truth_rows(CAL, "NSE", DAYS[:5], step=300))
    client = FakeClient(
        db={("X", "NSE", "5m"): rows},
        transport_fail_before=dt.date(2030, 1, 1),
    )
    with pytest.raises(ServerError, match="could not reach the OpenAlgo server") as raised:
        _load(client, source="db", end=dt.date(2024, 1, 5), provider_options={"max_retries": 5})
    assert raised.value.transport and not raised.value.rate_limited
    # Historify keeps the old contract: the transport failure is not retried and
    # the fetch raises on the first failed attempt.
    assert len(client.history_calls("db")) == 1
    assert raised.value is not None


def test_historify_raises_on_a_5xx_without_spending_the_retry_budget():
    # The wider transient retry set is the broker walk's only; a db window gets
    # one request and raises, even though a 500 would be retried for source="api".
    rows = historify_shape(truth_rows(CAL, "NSE", DAYS[:5], step=300))
    client = FakeClient(db={("X", "NSE", "5m"): rows})
    client.queue(500, None, persistent=True)
    with pytest.raises(ServerError, match="HTTP 500"):
        _load(client, source="db", end=dt.date(2024, 1, 5), provider_options={"max_retries": 5})
    assert len(client.history_calls("db")) == 1


# --- the retryable status table ------------------------------------------------------


@pytest.mark.parametrize(
    "status, retryable",
    [
        (200, False),
        (400, False),
        (401, False),
        (403, False),
        (404, False),
        (408, True),
        (429, True),
        (500, True),
        (502, True),
        (503, True),
        (504, True),
    ],
)
def test_only_408_429_and_5xx_answers_are_retried(status, retryable):
    assert is_retryable_status(status) is retryable


def test_a_retryable_5xx_is_retried_and_then_converges_when_it_persists():
    # 503 uses the same backoff and retry budget as 429; once they are spent the
    # broker walk converges with a server_error stop, the newest window kept.
    clock = FakeClock()
    client = _client()
    newest = broker_shape(truth_rows(CAL, "NSE", [END], step=300))
    client.queue(200, {"status": "success", "data": newest})
    client.queue(503, {"status": "error", "message": "maintenance"}, persistent=True)
    result = _load(
        client, clock=clock, broker="zerodha", provider_options={"max_retries": 2},
    )  # fmt: skip
    fetch = result.meta.fetch
    assert fetch.windows_fetched == 1
    assert fetch.stop_reason == "server_error"
    assert "HTTP 503" in fetch.stopped
    assert (fetch.requests, fetch.retries) == (4, 2)
    assert [sleep for sleep in clock.sleeps if sleep >= 1.0] == [1.0, 2.0]  # backoff only


def test_a_5xx_that_clears_on_retry_succeeds_without_losing_windows():
    clock = FakeClock()
    client = _client(days=DAYS[:10])
    client.queue(503, {"status": "error", "message": "maintenance"})
    result = _load(client, end=dt.date(2024, 1, 10), clock=clock, broker="zerodha")
    fetch = result.meta.fetch
    assert fetch.stopped is None and fetch.stop_reason is None
    assert (fetch.requests, fetch.retries) == (2, 1)
    assert len(result.bars) == BARS_PER_DAY_5M * 8


def test_an_unknown_symbol_still_raises_instead_of_converging():
    client = FakeClient()
    client.queue(
        400,
        {"status": "error", "message": "Symbol 'X' not found for exchange 'NSE'. Please verify."},
    )
    with pytest.raises(SymbolNotFoundError):
        _load(client, broker="zerodha", end=dt.date(2024, 1, 5))


# --- the broker history start stays its own stop reason ------------------------------


def test_broker_history_start_and_a_failure_stop_are_distinct_reasons():
    # The four machine-readable stop reasons are distinct codes.
    assert len({STOP_BROKER_START, STOP_RATE_LIMITED, STOP_TRANSPORT, STOP_SERVER_ERROR}) == 4
    # The empty first window of the oldest range: the history start, as before.
    held = [day for day in DAYS if day >= dt.date(2024, 5, 10)]
    result = _load(_client(days=held))
    fetch = result.meta.fetch
    assert fetch.stop_reason == STOP_BROKER_START
    assert fetch.stopped.startswith("the broker returned no bars")
    # A complete fetch has neither a reason nor words.
    complete = _load(_client())
    assert complete.meta.fetch.stopped is None and complete.meta.fetch.stop_reason is None


def test_rows_are_returned_sorted_and_an_empty_fetch_is_an_array_not_none():
    from openalgo.backtest.data.fetching import HistoryFetcher, Reply

    class StubTransport:
        def post(self, endpoint, payload):
            if payload["end_date"] == "2024-01-31":
                # Timestamps deliberately out of order; the result must be sorted.
                rows = [
                    {
                        "timestamp": at_ist(dt.date(2024, 1, 2), 9, 15) // 10**9,
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "volume": 1,
                    },
                    {
                        "timestamp": at_ist(dt.date(2024, 1, 1), 9, 15) // 10**9,
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "volume": 1,
                    },
                ]
                return Reply(200, {"status": "success", "data": broker_shape(rows)})
            return Reply(200, {"status": "success", "data": []})

    series = ("X", "NSE", "5m")
    fetcher = HistoryFetcher(StubTransport(), sleep=lambda seconds: None, clock=lambda: 0.0)
    records, report = fetcher.collect(
        series,
        [(dt.date(2024, 1, 1), dt.date(2024, 1, 31))],
        "api",
        31,
        0.0,
        completed_sessions=lambda first, last: 0,
    )
    assert list(records["timestamp"]) == sorted(records["timestamp"])
    assert report.stop_reason is None  # empty windows without a closed session do not end it

    def empty_post(endpoint, payload):
        return Reply(200, {"status": "success", "data": []})

    stub = StubTransport()
    stub.post = empty_post
    fetcher = HistoryFetcher(stub, sleep=lambda seconds: None, clock=lambda: 0.0)
    records, report = fetcher.collect(
        series,
        [(dt.date(2024, 1, 6), dt.date(2024, 1, 7))],
        "api",
        2,
        0.0,
        completed_sessions=lambda first, last: 0,
    )  # a weekend window: no closed session, so the walk reads every window
    assert len(records) == 0 and records is not None
    assert report.windows_fetched == report.windows_planned
    assert report.stop_reason is None


# --- raw rows -----------------------------------------------------------------------


def test_raw_rows_keep_epoch_seconds_and_values_as_the_server_sent_them():
    rows = broker_shape(truth_rows(CAL, "NSE", [START], step=300))[:2]
    records = rows_to_records(rows)
    assert records["timestamp"].tolist() == [
        at_ist(START, 9, 15) // 10**9,
        at_ist(START, 9, 20) // 10**9,
    ]
    assert records["close"].tolist() == [rows[0]["close"], rows[1]["close"]]
    assert records["oi"].tolist() == [0.0, 0.0]  # the zero the server added is kept as it is


@pytest.mark.parametrize(
    "bad_rows, error, message",
    [
        (lambda rows: [dict(r, timestamp="2024-01-01 09:15:00+05:30") for r in rows],
         TimestampError, "expected epoch seconds"),
        (lambda rows: [dict(r, timestamp=r["timestamp"] * 1000) for r in rows],
         TimestampError, "milliseconds"),
        (lambda rows: [dict(r, timestamp=r["timestamp"] + 0.5) for r in rows],
         TimestampError, "whole epoch seconds"),
        (lambda rows: [{"timestamp": r["timestamp"], "open": 1.0} for r in rows],
         DataError, "lack the fields"),
        (lambda rows: [dict(r, close="n/a") for r in rows], DataError, "not a number"),
    ],
    ids=["date strings", "milliseconds", "fractions", "missing prices", "text price"],
)  # fmt: skip
def test_raw_rows_that_are_not_epoch_seconds_with_prices_are_rejected(bad_rows, error, message):
    rows = truth_rows(CAL, "NSE", [START])[:5]
    with pytest.raises(error, match=message):
        rows_to_records(bad_rows(rows))
