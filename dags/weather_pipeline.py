"""Pure ETL logic for the ``weather_data_pipeline`` DAG.

The API and database logic lives here, outside the Airflow DAG module, so it
can be exercised with fake HTTP responses and a fake database hook without a
running Airflow instance, an OpenWeather API key or a PostgreSQL database.

The DAG module (:mod:`ML_Data_ETL_dag`) only resolves Airflow connections and
forwards XCom payloads to the functions below.
"""

import json
import logging
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10
REQUIRED_RESPONSE_FIELDS = ("main", "wind", "weather")
TARGET_FIELDS = [
    "city",
    "date",
    "temperature",
    "humidity",
    "wind_speed",
    "weather_condition",
]

# Upsert on the (city, date) unique index created by docker/init-weather-db.sql,
# so re-running the same batch updates the existing row instead of duplicating.
UPSERT_SQL = """
INSERT INTO weather (
    city, date, temperature, humidity, wind_speed, weather_condition
)
VALUES {values}
ON CONFLICT (city, date) DO UPDATE SET
    temperature = EXCLUDED.temperature,
    humidity = EXCLUDED.humidity,
    wind_speed = EXCLUDED.wind_speed,
    weather_condition = EXCLUDED.weather_condition
""".strip()


class WeatherApiError(Exception):
    """Raised when a single city's API response cannot be turned into a row."""


def _validate_response_body(data):
    """Make sure the response carries the main/wind/weather sections."""
    if not isinstance(data, dict):
        raise WeatherApiError("response body is not a JSON object")
    missing = [field for field in REQUIRED_RESPONSE_FIELDS if field not in data]
    if missing:
        raise WeatherApiError(
            "response body missing field(s): " + ", ".join(missing)
        )
    if not isinstance(data["weather"], list) or not data["weather"]:
        raise WeatherApiError("response body field 'weather' is empty")


def _build_record(city, data, iso_date):
    """Map an OpenWeather payload onto the existing weather table fields."""
    return {
        "city": city,
        "date": iso_date,
        "temperature": data["main"]["temp"],
        "humidity": data["main"]["humidity"],
        "wind_speed": data["wind"]["speed"],
        "weather_condition": data["weather"][0]["description"],
    }


def fetch_weather_data(locations, connection, http_get=requests.get, utc_now=None):
    """Fetch current weather for every city, tolerating per-city failures.

    A failed request, a timeout or a body missing ``main``/``wind``/``weather``
    is recorded as ``{"city": ..., "reason": ...}`` and collection continues
    with the remaining cities. The task only fails when *every* city fails.

    Returns a JSON-serialisable payload suitable for XCom::

        {"weather_data": [<records>], "failures": [{"city", "reason"}, ...]}

    ``http_get`` and ``utc_now`` are injectable so tests never touch the
    network or depend on wall-clock time.
    """
    api_key = connection.extra_dejson.get("api_key")
    base_url = connection.host
    iso_date = (utc_now or datetime.now(timezone.utc)).date().isoformat()  # noqa: UP017

    weather_data = []
    failures = []

    for location in locations:
        city = location["city"]
        try:
            response = http_get(
                base_url,
                params={
                    "lat": location["lat"],
                    "lon": location["lon"],
                    "appid": api_key,
                    "units": "metric",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                raise WeatherApiError(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )
            data = response.json()
            _validate_response_body(data)
            weather_data.append(_build_record(city, data, iso_date))
        # Any failure (timeout, connection error, bad JSON, missing field)
        # must be recorded for this city while the other cities proceed.
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
            failures.append({"city": city, "reason": reason})
            logger.error("Weather fetch failed for %s: %s", city, reason)

    if locations and len(failures) == len(locations):
        raise RuntimeError(
            f"Weather fetch failed for every city ({len(failures)}); "
            f"failures: {failures}"
        )

    logger.info(
        "Weather fetch finished: %s succeeded, %s failed.",
        len(weather_data),
        len(failures),
    )
    return {"weather_data": weather_data, "failures": failures}


def _coerce_date(value):
    """Accept both ISO date strings (XCom) and date objects."""
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return value


def _normalize_payload(payload):
    """Accept the new XCom dict, its JSON string, or a bare record list."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, list):
        weather_data, failures = payload, []
    else:
        weather_data = payload.get("weather_data", [])
        failures = payload.get("failures", [])

    if not isinstance(weather_data, list) or not all(
        isinstance(record, dict) for record in weather_data
    ):
        raise ValueError(
            "Invalid format for weather_data; expected a list of dictionaries."
        )
    return weather_data, failures


def insert_weather_data(payload, hook):
    """Upsert weather rows by ``(city, date)``.

    Duplicate records inside one batch are collapsed first, then every row is
    written with ``INSERT ... ON CONFLICT`` so re-running a batch updates the
    existing row instead of inserting a second one.

    Returns a summary ``{"inserted": N, "failures": [...]}`` (also pushed to
    XCom) and logs the written count plus the failed-city list.
    """
    weather_data, failures = _normalize_payload(payload)

    # In-batch de-duplication: last occurrence wins for the same (city, date).
    deduped = {}
    for record in weather_data:
        key = (record["city"], _coerce_date(record["date"]))
        deduped[key] = {**record, "date": key[1]}

    rows = [
        tuple(record[field] for field in TARGET_FIELDS)
        for record in deduped.values()
    ]

    if rows:
        placeholders = ", ".join(
            "(" + ", ".join(["%s"] * len(TARGET_FIELDS)) + ")" for _ in rows
        )
        hook.run(
            UPSERT_SQL.format(values=placeholders),
            parameters=[value for row in rows for value in row],
        )

    logger.info(
        "Successfully wrote %s weather rows (upsert on (city, date)).",
        len(rows),
    )
    if failures:
        failed_cities = [item["city"] for item in failures]
        logger.warning(
            "%s city/cities produced no row: %s",
            len(failed_cities),
            ", ".join(failed_cities),
        )

    return {"inserted": len(rows), "failures": failures}
