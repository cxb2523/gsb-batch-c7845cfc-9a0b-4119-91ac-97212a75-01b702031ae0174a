"""Raw history from an OpenAlgo server: transport, chunking, pacing and backoff.

Why not `client.history()`: the SDK turns the response into a DataFrame whose
index is tz-aware IST for intraday intervals, naive for D, W and M and shifted by
5 hours 30 minutes for Q and Y, and it flattens HTTP errors into a message string.
The data layer needs the raw epoch seconds and the HTTP status (429 must be backed
off), so it posts to the same endpoint itself, through the pooled `httpx.Client`
that every REST mixin of `openalgo.api` shares as `client.client` (with
`client.base_url`, `client.headers`, `client.timeout` and `client.api_key`). No
second pool is opened. A client without those attributes but with the SDK's
`_post(endpoint, payload)` helper is used through that helper instead.

`HistoryFetcher` sends the requests, never closer together than the pacing, backs
off on HTTP 429 (and, during a broker window walk, on HTTP 408, the 5xx and a
transport failure), and walks a date range window by window. The window sizes
and the pacing per broker are data in `brokers.py`.

A broker walk ("api") converges instead of failing the whole fetch: when a
window keeps being rate limited, cannot be transported, or answers with a final
server error, the windows already answered are kept (sorted by timestamp, an
empty array rather than None when none were answered) and `FetchReport` says why
the walk stopped and how many requests and retries the fetcher made. The empty
window that marks the broker's history start remains its own stop reason.
Historify ("db") never converges: it is read oldest first and a failed window
raises, as a filled database is expected to answer every window.
"""

import datetime as dt
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .._layout import BAR_COLUMNS, NANOS_PER_SECOND, PRICE_COLUMNS
from .._messages import counted
from ..errors import ConfigError, DataError, ServerError, SymbolNotFoundError, TimestampError
from ._sessions import ist_days

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_REQUEST_TIMEOUT = 408
HTTP_TOO_MANY_REQUESTS = 429
# Server answers a fetch may ask again for, with the same backoff as HTTP 429:
# 408 (the request timed out at the server) and every 5xx (a proxy or the
# history service momentarily failing). Every other non-200 answer is final:
# a 4xx names a request the server will not answer differently next time.
RETRYABLE_STATUSES = frozenset({HTTP_REQUEST_TIMEOUT, HTTP_TOO_MANY_REQUESTS, *range(500, 600)})
# How the server's history service words its HTTP 400 for a symbol its master contract
# does not hold: "Symbol 'X' not found for exchange 'NSE'. Please verify ..."
# (`validate_symbol_exchange` in services/history_service.py of the OpenAlgo server).
UNKNOWN_SYMBOL_MARK = "not found for exchange"

# Values of `FetchReport.stop_reason`: why a broker walk stopped before the
# oldest window. None means every window was read.
STOP_BROKER_START = "broker_history_start"
STOP_RATE_LIMITED = "rate_limited"
STOP_TRANSPORT = "transport_failure"
STOP_SERVER_ERROR = "server_error"


def is_retryable_status(status: int) -> bool:
    """True for the HTTP answers a fetch backs off and asks again: HTTP 408,
    HTTP 429 and the 5xx server errors. A 4xx other than 408/429 is final."""
    return status in RETRYABLE_STATUSES


# A raw history row as the server returns it: epoch seconds and the bar values.
# Volume and open interest are NaN when the server sent none.
RAW_DTYPE = np.dtype([("timestamp", "<i8")] + [(name, "<f8") for name in BAR_COLUMNS[2:]])

# Defaults for the answers a retry can help (HTTP 429 first among them). One
# second, doubling, is how the server's own broker adapters retry a rate-limited
# chunk. Five retries wait 31 seconds in total, well past the 10 seconds after
# which the server refuses a queued data request with 429
# (utils/broker_backpressure.py, BROKER_MAX_QUEUE_WAIT_SECONDS).
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_BACKOFF_MAX_SECONDS = 60.0

# Epoch seconds reach 1e11 in the year 5138; anything at or above is not seconds
# (epoch milliseconds are about 1.7e12 today).
_EPOCH_SECONDS_LIMIT = 1e11
# Longest server message quoted in an error or a warning.
_MESSAGE_LIMIT = 300
# `error_type` values the SDK's `_post` returns when no HTTP response arrived.
_SDK_TRANSPORT_FAILURES = ("timeout_error", "connection_error", "http_error", "unknown_error")

Series = tuple[str, str, str]  # (symbol, exchange, interval) as OpenAlgo strings
DateRange = tuple[dt.date, dt.date]  # (first, last) IST dates, both inclusive


def bucket_days(records: np.ndarray) -> np.ndarray:
    """Day number (days since 1970-01-01) of the IST date each raw row is stamped on."""
    return ist_days(records["timestamp"] * NANOS_PER_SECOND)


@dataclass(frozen=True)
class Reply:
    """One HTTP answer: the status, the JSON body when it is an object, the raw
    text otherwise, and the Retry-After header in seconds when the server sent one."""

    status: int
    body: dict | None
    text: str = ""
    retry_after_seconds: float | None = None

    def message(self) -> str:
        """The server's message, cut to a length that fits an error message."""
        if self.body is not None and self.body.get("message") is not None:
            return str(self.body["message"])[:_MESSAGE_LIMIT]
        return (self.text or "no message")[:_MESSAGE_LIMIT]

    def succeeded(self) -> bool:
        """True for HTTP 200 with `"status": "success"` in the body."""
        return self.status == HTTP_OK and (self.body or {}).get("status") == "success"


class Transport:
    """Posts JSON to the OpenAlgo REST API through an SDK client (duck-typed).

    Raises ConfigError at construction when the client offers neither the pooled
    HTTP client attributes nor the `_post` helper, or has no API key.
    """

    def __init__(self, client: object) -> None:
        if client is None:
            raise ConfigError(
                "an OpenAlgo client is required: pass client=openalgo.api(api_key=..., host=...)"
            )
        pool = getattr(client, "client", None)
        base_url = getattr(client, "base_url", None)
        self._through_pool = bool(callable(getattr(pool, "post", None)) and base_url)
        if not self._through_pool and not callable(getattr(client, "_post", None)):
            raise ConfigError(
                "client must be an openalgo.api instance, or an object with the same "
                "api_key, base_url, headers and pooled HTTP client attributes: the provider "
                "reads raw history rows through the client's own connection pool"
            )
        if not getattr(client, "api_key", None):
            raise ConfigError("client has no api_key")
        self._client = client

    def post(self, endpoint: str, payload: dict) -> Reply:
        """POST `payload` (plus the API key) to `endpoint` and return the Reply.

        Raises ServerError when no HTTP response arrives. The API key is never part
        of an error message.
        """
        client = self._client
        body = {"apikey": client.api_key, **payload}
        if not self._through_pool:
            return self._post_through_sdk(endpoint, body)
        options = {"json": body}
        # Passed only when the client defines them: for httpx an explicit
        # timeout=None would switch the timeout off.
        for name in ("headers", "timeout"):
            if getattr(client, name, None) is not None:
                options[name] = getattr(client, name)
        try:
            response = client.client.post(client.base_url + endpoint, **options)
        except Exception as exc:
            # Any failure of the HTTP library (timeout, refused connection, TLS)
            # means the same to the caller. Its message names the URL, never the
            # request body, so the API key cannot leak through it.
            raise ServerError(
                f"could not reach the OpenAlgo server for '{endpoint}': "
                f"{type(exc).__name__}: {exc}",
                transport=True,
            ) from exc
        try:
            data = response.json()
        except ValueError:
            data = None  # not JSON (a proxy error page): the status and text still count
        try:
            retry_after = float(response.headers.get("Retry-After"))
        except (AttributeError, TypeError, ValueError):
            retry_after = None  # absent, or given as a date
        text = str(getattr(response, "text", "") or "")
        return Reply(
            int(response.status_code), data if isinstance(data, dict) else None, text, retry_after
        )

    def _post_through_sdk(self, endpoint: str, body: dict) -> Reply:
        data = self._client._post(endpoint, body)
        if not isinstance(data, dict):
            raise DataError(f"unexpected reply from the client for '{endpoint}'")
        if "code" in data:
            return Reply(int(data["code"]), data, str(data.get("raw_response") or ""))
        if data.get("status") != "success" and data.get("error_type") in _SDK_TRANSPORT_FAILURES:
            raise ServerError(
                f"could not reach the OpenAlgo server for '{endpoint}': {data.get('message')}",
                transport=True,
            )
        return Reply(HTTP_OK, data)


def plan_chunks(start: dt.date, end: dt.date, window_days: int) -> list[DateRange]:
    """Split the inclusive date range into windows of at most `window_days` days.

    Returns [(first_day, last_day), ...] oldest first. Windows are anchored at
    `end`, so the newest one is full-sized and only the oldest can be shorter. They
    do not overlap and leave no gap.

    Raises ConfigError when `window_days` is below 1 or `start` is after `end`.
    """
    if window_days < 1:
        raise ConfigError(f"chunk window must be at least 1 day, got {window_days}")
    if start > end:
        raise ConfigError(f"start {start} is after end {end}")
    chunks = []
    high = end
    while high >= start:
        low = max(start, high - dt.timedelta(days=window_days - 1))
        chunks.append((low, high))
        high = low - dt.timedelta(days=1)
    chunks.reverse()
    return chunks


def rows_to_records(rows: Sequence[dict], tag: str = "history") -> np.ndarray:
    """Read raw `/history` records into an array of RAW_DTYPE.

    The `timestamp` field must hold epoch seconds, which OpenAlgo uses for both
    sources. `tag` names the series in error messages.

    Raises DataError when a required field is missing or a value is not a number,
    and TimestampError when the timestamps are strings, fractions, missing or not
    in the range of epoch seconds: a convention is never guessed.
    """
    try:
        table = pd.DataFrame.from_records(rows)
    except (TypeError, ValueError, AttributeError) as exc:
        raise DataError(f"{tag}: unexpected history payload ({exc})") from exc
    missing = [name for name in ("timestamp", *PRICE_COLUMNS) if name not in table.columns]
    if missing:
        raise DataError(f"{tag}: history rows lack the fields {missing}")
    raw = table["timestamp"]
    if pd.api.types.is_bool_dtype(raw.dtype) or not pd.api.types.is_numeric_dtype(raw.dtype):
        raise TimestampError(
            f"{tag}: expected epoch seconds in the 'timestamp' field, got {raw.iloc[0]!r}. "
            "The provider reads raw epochs only and does not parse date strings."
        )
    epoch_seconds = raw.to_numpy(dtype="float64", na_value=np.nan)
    if not np.all(np.isfinite(epoch_seconds)):
        absent = int((~np.isfinite(epoch_seconds)).sum())
        raise TimestampError(f"{tag}: {counted(absent, 'row has', 'rows have')} no timestamp")
    if np.any(epoch_seconds != np.floor(epoch_seconds)):
        raise TimestampError(f"{tag}: timestamps are not whole epoch seconds")
    if epoch_seconds.min() <= 0 or epoch_seconds.max() >= _EPOCH_SECONDS_LIMIT:
        raise TimestampError(
            f"{tag}: timestamps are outside the range of epoch seconds (for example "
            f"{epoch_seconds.max():.0f}); they may be milliseconds."
        )
    records = np.zeros(len(table), dtype=RAW_DTYPE)
    records["timestamp"] = epoch_seconds.astype("int64")
    try:
        for name in RAW_DTYPE.names[1:]:
            present = name in table.columns
            records[name] = (
                table[name].to_numpy(dtype="float64", na_value=np.nan) if present else np.nan
            )
    except (TypeError, ValueError) as exc:
        raise DataError(f"{tag}: a history value is not a number ({exc})") from exc
    return records


@dataclass
class FetchReport:
    """How a fetch went.

    window_days: the chunk window used. windows_planned, windows_fetched: windows
    the range was split into and windows that were answered. stopped: why the walk
    stopped before the oldest window, in words (None when every window was read).
    stop_reason: the same fact as a machine-readable code (None when every window
    was read): STOP_BROKER_START for the broker's history start, STOP_RATE_LIMITED
    when HTTP 429 outlasted the retries, STOP_TRANSPORT when the server could not be
    reached after the retries, STOP_SERVER_ERROR for a final non-retryable server
    answer. This is how "the fetch converged on a failure" is told apart from "the
    broker's history starts here". requests, retries: totals of the fetcher after
    this fetch. answered: the (first, last) IST dates of each answered window as ISO
    strings, in the order asked (strings keep the report JSON-ready, as
    `LoadMetadata.to_dict` needs).
    """

    window_days: int
    windows_planned: int
    windows_fetched: int = 0
    stopped: str | None = None
    stop_reason: str | None = None
    requests: int = 0
    retries: int = 0
    answered: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class HistoryFetcher:
    """Paced, rate-limit aware history requests through one transport.

    max_retries, backoff_base_seconds, backoff_max_seconds: HTTP 429 is retried
    after `backoff_base_seconds`, doubling per attempt up to
    `backoff_max_seconds`; a Retry-After header lengthens a wait but never
    beyond the maximum. During a broker window walk the same backoff and budget
    cover HTTP 408, the 5xx and a transport that cannot deliver, so every kind
    of retry converges after at most `max_retries` retries instead of being
    retried for ever. A standalone request and a Historify walk keep to HTTP
    429 alone, raising every other failure at once.
    sleep and clock replace `time.sleep` and `time.monotonic`, so tests wait for
    nothing. requests, retries and waited_seconds count what was done.
    """

    transport: Transport
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS
    backoff_max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    requests: int = 0
    retries: int = 0
    waited_seconds: float = 0.0
    # True only while `collect` walks a broker's ("api") windows: that walk
    # retries HTTP 408, the 5xx and transport failures in addition to HTTP 429.
    # Historify's walk and single requests (`request`, `history` as the probe
    # and warm-up loads use them) and `ping` keep the old contract: only HTTP
    # 429 is backed off, every other failure raises at once.
    _retry_transient_answers: bool = field(default=False, init=False, repr=False)
    _last_request_at: float | None = field(default=None, init=False, repr=False)

    def request(self, endpoint: str, payload: dict, spacing_seconds: float) -> Reply:
        """Send one request, at least `spacing_seconds` after the previous one.

        HTTP 429 is always backed off with the doubling backoff. During a broker
        window walk (`_retry_transient_answers`) HTTP 408, the 5xx and a
        transport that cannot deliver join that set, all sharing one budget so
        the waits always converge after at most `max_retries` retries. A
        standalone request (the broker `ping`, the source="auto" probe, a
        warm-up load) and a Historify walk keep the old contract: every other
        failure, including a transport one, is returned or raised at once, so
        the probe fails fast and falls back to the broker. Raises ServerError
        when the retries are exhausted: `rate_limited` for an answer that
        stayed HTTP 429, `transport` for a server that could never be reached.
        """
        attempt = 0
        while True:
            self._pace(spacing_seconds)
            try:
                reply = self.transport.post(endpoint, payload)
            except ServerError as exc:
                # No HTTP response arrived (timeout, refused connection, TLS).
                # Only a broker window walk backs off and asks again; anywhere
                # else the failure raises at once, as it always did.
                self.requests += 1
                if not exc.transport or not self._retry_transient_answers:
                    raise
                if attempt >= self.max_retries:
                    raise
                self._retry_wait(attempt, None)
                attempt += 1
                continue
            self.requests += 1
            # HTTP 429 is backed off for every endpoint. The wider transient set
            # (HTTP 408 and the 5xx) is retried only during a broker window walk:
            # the broker `ping`, the source="auto" probe and Historify fail fast
            # on a 5xx, so a database hiccup sends "auto" straight to the broker
            # instead of spending the whole retry budget first.
            retryable = reply.status == HTTP_TOO_MANY_REQUESTS or (
                is_retryable_status(reply.status)
                and endpoint == "history"
                and self._retry_transient_answers
            )
            if not retryable:
                return reply
            if attempt >= self.max_retries:
                if reply.status == HTTP_TOO_MANY_REQUESTS:
                    raise ServerError(
                        f"the OpenAlgo server kept answering HTTP 429 (rate limited) for "
                        f"'{endpoint}' after {counted(attempt, 'retry', 'retries')}: "
                        f"{reply.message()} Raise pacing= "
                        "or try again later.",
                        rate_limited=True,
                    )
                raise ServerError(
                    f"the OpenAlgo server kept answering HTTP {reply.status} for '{endpoint}' "
                    f"after {counted(attempt, 'retry', 'retries')}: {self._detail(reply)} "
                    "Try again later."
                )
            self._retry_wait(attempt, reply.retry_after_seconds)
            attempt += 1

    @staticmethod
    def _detail(reply: Reply) -> str:
        """The wording of a failed answer: the server's message, or the same
        'body is not JSON' note `history` gives a non-JSON proxy page."""
        if reply.body is None:
            return "the body is not JSON; check the host of the client"
        return reply.message()

    def _pace(self, spacing_seconds: float) -> None:
        """Wait until `spacing_seconds` have passed since the previous request."""
        now = self.clock()
        if self._last_request_at is not None:
            remaining = self._last_request_at + spacing_seconds - now
            if remaining > 0:
                self._wait(remaining)
                now = self.clock()
        self._last_request_at = now

    def _retry_wait(self, attempt: int, retry_after_seconds: float | None) -> None:
        """One backoff wait of the `attempt`-th retry (0-based), honouring
        Retry-After but never waiting beyond the configured maximum."""
        wait = self.backoff_base_seconds * (2**attempt)
        if retry_after_seconds is not None and retry_after_seconds > 0:
            wait = max(wait, retry_after_seconds)
        self.retries += 1
        self._wait(min(self.backoff_max_seconds, wait))

    def _wait(self, seconds: float) -> None:
        self.waited_seconds += float(seconds)
        self.sleep(seconds)

    def history(
        self, series: Series, window: DateRange, source: str, spacing_seconds: float
    ) -> np.ndarray:
        """The raw rows of one history request for the inclusive IST date window.

        An empty array means the source has no rows for the window (Historify says
        so with a JSON 404).

        Raises SymbolNotFoundError when the server answers that its master contract
        does not hold the symbol, and ServerError for every other answer that is not
        a success, naming the HTTP status, and when the retries on a rate-limited,
        retryable server or transport failure are exhausted.
        """
        symbol, exchange, interval = series
        payload = {
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "start_date": window[0].isoformat(),
            "end_date": window[1].isoformat(),
            "source": source,
        }
        tag = f"{exchange}:{symbol} {interval}"
        reply = self.request("history", payload, spacing_seconds)
        rows = (reply.body or {}).get("data") or []
        if reply.succeeded() and isinstance(rows, list):
            return rows_to_records(rows, tag) if rows else np.zeros(0, dtype=RAW_DTYPE)
        if reply.status == HTTP_NOT_FOUND and source == "db" and reply.body is not None:
            return np.zeros(0, dtype=RAW_DTYPE)
        detail = reply.message()
        if reply.body is None:
            detail = "the body is not JSON; check the host of the client"
        message = (
            f"{tag}: the OpenAlgo history request for {window[0]} to {window[1]} "
            f"(source={source!r}) failed (HTTP {reply.status}: {detail})"
        )
        if reply.status == HTTP_BAD_REQUEST and UNKNOWN_SYMBOL_MARK in detail:
            raise SymbolNotFoundError(message, symbol, exchange)
        raise ServerError(message)

    def collect(
        self,
        series: Series,
        ranges: Sequence[DateRange],
        source: str,
        window_days: int,
        spacing_seconds: float,
        completed_sessions: Callable[[dt.date, dt.date], int],
    ) -> tuple[np.ndarray, FetchReport]:
        """Fetch the inclusive IST date `ranges` window by window.

        Historify ("db") is read oldest first, every window, and a window that
        fails raises: a filled database is expected to answer each window, so a
        failure is never taken for the end of history and never hidden.

        A broker ("api") is read newest first. Two things end its walk cleanly,
        keeping every window already answered:

        - the first window of the oldest range that holds nothing although
          `completed_sessions(first, last)` says a session has closed in it: that
          is where the broker's history ends (`stop_reason=STOP_BROKER_START`). A
          window without such a session (a weekend, a holiday, today before the
          close) never ends the walk, and neither does one in a later range:
          sessions the cache has settled lie before it, so it is a gap, not the
          start of the broker's history;
        - a window that cannot be answered: HTTP 429, a transport failure or a
          retryable 5xx/408 that outlasts the retries (`STOP_RATE_LIMITED`,
          `STOP_TRANSPORT`), or a final non-retryable server answer such as
          HTTP 401 (`STOP_SERVER_ERROR`, at once, without spending the retries).
          The rows of the windows answered before it are returned, sorted by
          timestamp; when no window was answered the array is empty, never None.
          An unknown symbol still raises SymbolNotFoundError, since converging
          would hide it.

        Returns (rows sorted by timestamp, FetchReport). `stopped` and
        `stop_reason` are None when every window was read; `requests` and
        `retries` carry the fetcher totals, so the report alone tells a converged
        fetch apart from a complete one and from the broker's history start.
        """
        windows = [
            (number, window)
            for number, date_range in enumerate(sorted(ranges))
            for window in plan_chunks(*date_range, window_days)
        ]
        report = FetchReport(window_days, len(windows))
        parts = []
        ordered = windows if source == "db" else reversed(windows)
        # Only the broker walk retries the wider transient set; Historify keeps
        # the old contract (HTTP 429 alone, everything else raised at once).
        self._retry_transient_answers = source == "api"
        try:
            for number, window in ordered:
                try:
                    records = self.history(series, window, source, spacing_seconds)
                except SymbolNotFoundError:
                    raise
                except ServerError as error:
                    if source == "db":
                        raise
                    self._converge(report, window, error)
                    break
                report.windows_fetched += 1
                report.answered.append((window[0].isoformat(), window[1].isoformat()))
                if len(records):
                    parts.append(records)
                elif source == "api" and number == 0 and completed_sessions(*window):
                    report.stop_reason = STOP_BROKER_START
                    report.stopped = (
                        f"the broker returned no bars for {window[0]} to {window[1]}, so the "
                        f"history is taken to start after {window[1]}; nothing older was asked for"
                    )
                    break
        finally:
            self._retry_transient_answers = False
        report.requests, report.retries = self.requests, self.retries
        records = np.concatenate(parts) if parts else np.zeros(0, dtype=RAW_DTYPE)
        return records[np.argsort(records["timestamp"], kind="stable")], report

    def _converge(self, report: FetchReport, window: DateRange, error: ServerError) -> None:
        """Record why a broker window could not be answered, ending the walk while
        keeping the windows already answered (see `collect`)."""
        first, last = window
        if error.rate_limited:
            report.stop_reason = STOP_RATE_LIMITED
            report.stopped = (
                f"the server stayed rate limited (HTTP 429) for the window {first} to {last} "
                f"after the allowed retries, so no older window was asked for; the windows "
                f"already answered were kept. {error}"
            )
        elif error.transport:
            report.stop_reason = STOP_TRANSPORT
            report.stopped = (
                f"the OpenAlgo server could not be reached for the window {first} to {last} "
                f"after the allowed retries, so no older window was asked for; the windows "
                f"already answered were kept. {error}"
            )
        else:
            report.stop_reason = STOP_SERVER_ERROR
            report.stopped = (
                f"the server answered the window {first} to {last} with a final error (an answer "
                f"no retry could change, or one that stayed an error after the allowed retries), "
                f"so no older window was asked for; the windows already answered were kept. {error}"
            )


__all__ = [
    "DEFAULT_BACKOFF_BASE_SECONDS",
    "DEFAULT_BACKOFF_MAX_SECONDS",
    "DEFAULT_MAX_RETRIES",
    "RAW_DTYPE",
    "DateRange",
    "FetchReport",
    "HistoryFetcher",
    "Reply",
    "Series",
    "STOP_BROKER_START",
    "STOP_RATE_LIMITED",
    "STOP_SERVER_ERROR",
    "STOP_TRANSPORT",
    "Transport",
    "bucket_days",
    "is_retryable_status",
    "plan_chunks",
    "rows_to_records",
]
