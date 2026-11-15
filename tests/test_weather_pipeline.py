"""Unit tests for the weather ETL logic using fakes only.

No Airflow, no OpenWeather API and no PostgreSQL: the HTTP layer is replaced
with canned responses (including timeouts, HTTP errors and malformed bodies)
and the database is replaced with an in-memory fake PostgresHook that
emulates the unique (city, date) index and ``ON CONFLICT`` upsert semantics.
"""

import json
import logging
from datetime import datetime, timezone

import pytest
import requests

from dags import weather_pipeline as wp

FIXED_NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
ISO_DATE = FIXED_NOW.date().isoformat()


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload


def success_payload(temperature=10.0):
    return {
        "main": {"temp": temperature, "humidity": 80},
        "wind": {"speed": 5.5},
        "weather": [{"description": "cloudy"}],
    }


class FakeHttpGet:
    """Routes requests per latitude: success, raised error, HTTP error or body."""

    def __init__(self, routes):
        # routes: {lat: ("ok", payload) | ("raise", exc) | ("status", code, text)
        #          | ("body", dict)}
        self.routes = routes
        self.calls = []

    def __call__(self, url, params=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        route = self.routes[params["lat"]]
        kind = route[0]
        if kind == "raise":
            raise route[1]
        if kind == "status":
            return FakeResponse(status_code=route[1], text=route[2])
        if kind == "body":
            return FakeResponse(status_code=200, payload=route[1])
        return FakeResponse(status_code=200, payload=route[1])


class FakeConnection:
    def __init__(self, host="https://api.example/weather", api_key="secret"):
        self.host = host
        self.extra_dejson = {"api_key": api_key}


class FakePostgresHook:
    """In-memory stand-in for PostgresHook enforcing the (city, date) upsert."""

    def __init__(self):
        self.rows = {}  # (city, date) -> tuple
        self.run_calls = []

    def run(self, sql, parameters=None, *args, **kwargs):
        assert "ON CONFLICT (city, date) DO UPDATE" in sql
        self.run_calls.append({"sql": sql, "parameters": parameters})
        values = list(parameters)
        for start in range(0, len(values), len(wp.TARGET_FIELDS)):
            city, date, temperature, humidity, wind_speed, condition = values[
                start : start + len(wp.TARGET_FIELDS)
            ]
            # Mirrors INSERT ... ON CONFLICT DO UPDATE on the unique index.
            self.rows[(city, date)] = (
                city,
                date,
                temperature,
                humidity,
                wind_speed,
                condition,
            )

    def all_rows(self):
        return list(self.rows.values())


def make_locations(*lats):
    return [
        {"city": f"city-{lat}", "lat": lat, "lon": -float(lat)} for lat in lats
    ]


# ---------------------------------------------------------------------
# fetch_weather_data
# ---------------------------------------------------------------------
def test_partial_failure_other_cities_written_and_failure_list_complete(caplog):
    locations = make_locations(1, 2, 3, 4, 5)
    routes = {
        1: ("ok", success_payload(temperature=11.0)),
        2: ("raise", requests.exceptions.Timeout("timed out")),
        3: ("status", 500, "server error"),
        4: ("body", {"main": {"temp": 1}, "weather": [{"description": "x"}]}),
        5: ("body", {"main": {"temp": 1}, "wind": {"speed": 2}}),
    }
    http = FakeHttpGet(routes)

    with caplog.at_level(logging.ERROR, logger="dags.weather_pipeline"):
        result = wp.fetch_weather_data(
            locations, FakeConnection(), http_get=http, utc_now=FIXED_NOW
        )

    # One success keeps the existing fields and ISO date format.
    assert len(result["weather_data"]) == 1
    record = result["weather_data"][0]
    assert record == {
        "city": "city-1",
        "date": ISO_DATE,
        "temperature": 11.0,
        "humidity": 80,
        "wind_speed": 5.5,
        "weather_condition": "cloudy",
    }

    # Every failed city appears once, with a human-readable reason.
    failures = result["failures"]
    assert {f["city"] for f in failures} == {
        "city-2",
        "city-3",
        "city-4",
        "city-5",
    }
    reasons = {f["city"]: f["reason"] for f in failures}
    assert "Timeout" in reasons["city-2"]
    assert "HTTP 500" in reasons["city-3"]
    assert "wind" in reasons["city-4"]
    assert "weather" in reasons["city-5"]

    # The failure list must be findable in the logs and survive XCom
    # serialisation (XCom stores JSON-compatible values).
    assert "city-2" in caplog.text
    json.loads(json.dumps(result))

    # The configured timeout is still passed to the HTTP call.
    assert all(call["timeout"] == wp.REQUEST_TIMEOUT_SECONDS for call in http.calls)


def test_missing_or_empty_weather_list_is_recorded():
    locations = make_locations(1, 2)
    routes = {
        1: ("ok", success_payload()),
        2: ("body", {**success_payload(), "weather": []}),
    }
    result = wp.fetch_weather_data(
        locations, FakeConnection(), http_get=FakeHttpGet(routes), utc_now=FIXED_NOW
    )
    assert len(result["weather_data"]) == 1
    assert result["failures"][0]["city"] == "city-2"
    assert "weather" in result["failures"][0]["reason"]


def test_all_cities_fail_raises():
    locations = make_locations(1, 2)
    routes = {
        1: ("raise", requests.exceptions.ConnectionError("boom")),
        2: ("status", 401, "unauthorized"),
    }
    with pytest.raises(RuntimeError) as exc_info:
        wp.fetch_weather_data(
            locations, FakeConnection(), http_get=FakeHttpGet(routes), utc_now=FIXED_NOW
        )
    message = str(exc_info.value)
    assert "city-1" in message and "city-2" in message


def test_empty_locations_does_not_count_as_total_failure():
    result = wp.fetch_weather_data(
        [], FakeConnection(), http_get=FakeHttpGet({}), utc_now=FIXED_NOW
    )
    assert result == {"weather_data": [], "failures": []}


# ---------------------------------------------------------------------
# insert_weather_data
# ---------------------------------------------------------------------
def _record(city, date=ISO_DATE, temperature=10.0):
    return {
        "city": city,
        "date": date,
        "temperature": temperature,
        "humidity": 80,
        "wind_speed": 5.5,
        "weather_condition": "cloudy",
    }


def test_insert_upserts_and_surfaces_failure_summary(caplog):
    caplog.set_level(logging.INFO, logger="dags.weather_pipeline")
    hook = FakePostgresHook()
    payload = {
        "weather_data": [_record("Seattle"), _record("Boston")],
        "failures": [
            {"city": "Miami", "reason": "Timeout: timed out"},
            {"city": "Denver", "reason": "HTTP 500"},
        ],
    }

    summary = wp.insert_weather_data(payload, hook)

    assert summary["inserted"] == 2
    assert [f["city"] for f in summary["failures"]] == ["Miami", "Denver"]
    assert len(hook.all_rows()) == 2
    assert caplog.text.count("Successfully wrote 2") == 1
    assert "Miami" in caplog.text and "Denver" in caplog.text


def test_rerun_same_batch_keeps_one_row_per_city_date():
    hook = FakePostgresHook()
    batch = {
        "weather_data": [_record("Seattle", temperature=10.0), _record("Boston")],
        "failures": [],
    }

    first = wp.insert_weather_data(batch, hook)
    # Re-run the same batch (also exercise the JSON-string XCom path) with an
    # updated reading; the existing rows must be updated, not duplicated.
    rerun = json.loads(json.dumps(batch))
    rerun["weather_data"][0]["temperature"] = 12.5
    second = wp.insert_weather_data(json.dumps(rerun), hook)

    assert first["inserted"] == 2 and second["inserted"] == 2
    rows = hook.all_rows()
    assert len(rows) == 2
    seattle = next(row for row in rows if row[0] == "Seattle")
    assert seattle[2] == 12.5  # latest reading, no duplicate row

    # A third run containing an in-batch duplicate (same city, same date)
    # collapses to a single row.
    third_payload = {
        "weather_data": [
            _record("Seattle", temperature=13.0),
            _record("Seattle", temperature=14.0),
        ],
        "failures": [],
    }
    wp.insert_weather_data(third_payload, hook)
    seattle_rows = [row for row in hook.all_rows() if row[0] == "Seattle"]
    assert len(seattle_rows) == 1
    assert seattle_rows[0][2] == 14.0  # last occurrence in the batch wins


def test_insert_accepts_bare_record_list_for_backwards_compatibility():
    hook = FakePostgresHook()
    summary = wp.insert_weather_data([_record("Seattle")], hook)
    assert summary == {"inserted": 1, "failures": []}
    assert len(hook.all_rows()) == 1


def test_empty_batch_with_failures_makes_no_database_call():
    hook = FakePostgresHook()
    payload = {
        "weather_data": [],
        "failures": [{"city": "Miami", "reason": "Timeout: timed out"}],
    }
    summary = wp.insert_weather_data(payload, hook)
    assert summary["inserted"] == 0
    assert summary["failures"][0]["city"] == "Miami"
    assert hook.run_calls == []


def test_invalid_payload_raises():
    with pytest.raises(ValueError):
        wp.insert_weather_data({"weather_data": "not-a-list"}, FakePostgresHook())
