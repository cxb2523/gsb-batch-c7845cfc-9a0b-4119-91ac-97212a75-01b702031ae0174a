"""High-level tests for the Allocine API client."""

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest
import wreq
from freezegun import freeze_time

from allocine import Allocine, ShowtimeList
from allocine.client import _initial_theatrical_release_date


@pytest.fixture(scope="session")
def allocine() -> Allocine:
    return Allocine(cache=False)


def fail_requests_matching(monkeypatch, allocine: Allocine, predicate, error: Exception) -> None:
    """Make the HTTP transport raise ``error`` for URLs matching ``predicate``.

    Only the transport is patched: the responses still come from the recorded cassette,
    so the successful pages keep the real upstream payloads.
    """
    playback = allocine._client._fetch

    def fetch(url, expected_status, *args, **kwargs):
        if predicate(url):
            raise error
        return playback(url, expected_status, *args, **kwargs)

    monkeypatch.setattr(allocine._client, "_fetch", fetch)


def test_get_theater(allocine: Allocine, json_snapshot, allocine_vcr):
    theater = allocine.get_theater("P0645")

    assert asdict(theater) == json_snapshot


def test_get_theater_without_current_showtimes(allocine: Allocine, json_snapshot, allocine_vcr):
    theater = allocine.get_theater("W1021")

    assert asdict(theater) == json_snapshot


def test_search_theaters(allocine: Allocine, json_snapshot, allocine_vcr):
    theaters = allocine.search_theaters(geocode=83178)

    assert theaters == json_snapshot


def test_get_geocodes_lists_departments(allocine: Allocine, json_snapshot, allocine_vcr):
    locations = allocine.get_geocodes()

    assert [asdict(location) for location in locations] == json_snapshot


def test_get_geocodes_lists_department_cities(allocine: Allocine, json_snapshot, allocine_vcr):
    locations = allocine.get_geocodes(dept=29)

    assert [asdict(location) for location in locations] == json_snapshot


def test_search_theaters_by_department(allocine: Allocine, json_snapshot, allocine_vcr):
    theaters = allocine.search_theaters(dept=53)

    assert [asdict(theater) for theater in theaters] == json_snapshot


def test_search_theaters_by_zipcode(allocine: Allocine, json_snapshot, allocine_vcr):
    theaters = allocine.search_theaters(zipcode=29200)

    assert [asdict(theater) for theater in theaters] == json_snapshot


@pytest.mark.parametrize("kwargs", [{}, {"geocode": 95171, "dept": 29}])
def test_search_theaters_requires_one_location_filter(allocine: Allocine, kwargs):
    with pytest.raises(ValueError, match="Exactly one"):
        allocine.search_theaters(**kwargs)


def test_search_theaters_rejects_unknown_zipcode(allocine: Allocine, allocine_vcr):
    with pytest.raises(ValueError, match="No location found for zipcode '99999'"):
        allocine.search_theaters(zipcode=99999)


@freeze_time("2026-09-27")
def test_get_showtimes_defaults_to_today(allocine: Allocine, json_snapshot, allocine_vcr):
    showtimes = allocine.get_showtimes("G0FOX")

    assert [asdict(showtime) for showtime in showtimes] == json_snapshot


@freeze_time("2026-09-28")
def test_get_showtimes_clamps_past_from_date_to_today(allocine: Allocine, json_snapshot, allocine_vcr):
    showtimes = allocine.get_showtimes("G0FOX", from_date=date(2026, 9, 26))

    assert [asdict(showtime) for showtime in showtimes] == json_snapshot


@freeze_time("2026-09-27")
def test_get_showtimes_fetches_inclusive_date_range(allocine: Allocine, json_snapshot, allocine_vcr):
    showtimes = allocine.get_showtimes(
        "G0FOX",
        from_date=date(2026, 9, 27),
        to_date=date(2026, 9, 28),
    )

    assert [asdict(showtime) for showtime in showtimes] == json_snapshot


@freeze_time("2026-09-28")
def test_get_showtimes_treats_not_found_as_empty_without_retry(allocine: Allocine, allocine_vcr):
    showtimes = allocine.get_showtimes("C0125", from_date=date(2026, 9, 29))

    assert showtimes == []
    assert showtimes.is_complete


@freeze_time("2026-09-29")
def test_get_showtimes_stops_after_no_showtime_error(allocine: Allocine, json_snapshot, allocine_vcr):
    showtimes = allocine.get_showtimes("W0746", to_date=date(2026, 10, 5))

    assert [asdict(showtime) for showtime in showtimes] == json_snapshot
    assert showtimes.is_complete


@freeze_time("2026-09-26")
def test_get_showtimes_skips_to_next_showtime_date(allocine: Allocine, json_snapshot, allocine_vcr):
    showtimes = allocine.get_showtimes("W1021", to_date=date(2026, 10, 2))

    assert [asdict(showtime) for showtime in showtimes] == json_snapshot
    assert showtimes.is_complete


@freeze_time("2026-11-16")
def test_get_showtimes_fetches_multi_page_days(allocine: Allocine, json_snapshot, allocine_vcr):
    showtimes = allocine.get_showtimes(
        "P0535",
        from_date=date(2026, 11, 16),
        to_date=date(2026, 11, 17),
    )

    assert [asdict(showtime) for showtime in showtimes] == json_snapshot
    assert showtimes.is_complete
    assert showtimes.incomplete_dates == ()
    assert isinstance(showtimes, list)
    assert showtimes == list(showtimes)
    # Both days really span several pages of the recorded responses.
    assert {showtime.date for showtime in showtimes} == {date(2026, 11, 16), date(2026, 11, 17)}


@freeze_time("2026-11-16")
@pytest.mark.parametrize("allocine_vcr", ["get_showtimes_fetches_multi_page_days.json"], indirect=True)
def test_get_showtimes_keeps_fetched_pages_when_a_later_page_fails(allocine: Allocine, allocine_vcr, monkeypatch):
    fail_requests_matching(monkeypatch, allocine, lambda url: "/d-2026-11-16/p-2/" in url, wreq.TimeoutError("boom"))

    showtimes = allocine.get_showtimes(
        "P0535",
        from_date=date(2026, 11, 16),
        to_date=date(2026, 11, 17),
    )

    # Page 1 of the first day was kept, and the following day was still processed.
    assert {showtime.date for showtime in showtimes} == {date(2026, 11, 16), date(2026, 11, 17)}
    first_day = [showtime for showtime in showtimes if showtime.date == date(2026, 11, 16)]
    second_day = [showtime for showtime in showtimes if showtime.date == date(2026, 11, 17)]
    # 39 showtimes on the first page of 2026-11-16, 3 more on the page that failed.
    assert len(first_day) == 39
    # The next day was fetched completely (39 + 3).
    assert len(second_day) == 42
    # The interrupted day is flagged, the other one is not.
    assert showtimes.incomplete_dates == (date(2026, 11, 16),)
    assert not showtimes.is_complete
    assert not showtimes.is_date_complete(date(2026, 11, 16))
    assert showtimes.is_date_complete(date(2026, 11, 17))
    assert isinstance(showtimes.incomplete_errors[date(2026, 11, 16)], wreq.TimeoutError)
    # Legacy callers still get a plain list they can iterate over.
    assert isinstance(showtimes, list)
    assert showtimes == list(showtimes)


@freeze_time("2026-11-16")
@pytest.mark.parametrize("allocine_vcr", ["get_showtimes_fetches_multi_page_days.json"], indirect=True)
def test_get_showtimes_flags_each_incomplete_day_independently(allocine: Allocine, allocine_vcr, monkeypatch):
    fail_requests_matching(monkeypatch, allocine, lambda url: "/p-2/" in url, wreq.ConnectionError("boom"))

    showtimes = allocine.get_showtimes(
        "P0535",
        from_date=date(2026, 11, 16),
        to_date=date(2026, 11, 17),
    )

    assert showtimes.incomplete_dates == (date(2026, 11, 16), date(2026, 11, 17))
    assert all(showtime.date in {date(2026, 11, 16), date(2026, 11, 17)} for showtime in showtimes)
    assert len(showtimes) > 0


@freeze_time("2026-11-16")
@pytest.mark.parametrize("allocine_vcr", ["get_showtimes_fetches_multi_page_days.json"], indirect=True)
def test_get_showtimes_raises_when_the_first_page_fails(allocine: Allocine, allocine_vcr, monkeypatch):
    fail_requests_matching(monkeypatch, allocine, lambda url: "/d-2026-11-16/" in url, wreq.TimeoutError("boom"))

    with pytest.raises(wreq.TimeoutError):
        allocine.get_showtimes("P0535", from_date=date(2026, 11, 16), to_date=date(2026, 11, 17))


@freeze_time("2026-11-16")
@pytest.mark.parametrize("allocine_vcr", ["get_showtimes_fetches_multi_page_days.json"], indirect=True)
def test_get_showtimes_raises_when_a_later_day_first_page_fails(allocine: Allocine, allocine_vcr, monkeypatch):
    fail_requests_matching(monkeypatch, allocine, lambda url: "/d-2026-11-17/" in url, wreq.TimeoutError("boom"))

    with pytest.raises(wreq.TimeoutError):
        allocine.get_showtimes("P0535", from_date=date(2026, 11, 16), to_date=date(2026, 11, 17))


@freeze_time("2026-09-26")
@pytest.mark.parametrize("allocine_vcr", ["get_showtimes_skips_to_next_showtime_date.json"], indirect=True)
def test_get_showtimes_does_not_flag_next_showtime_on_as_incomplete(allocine: Allocine, allocine_vcr):
    showtimes = allocine.get_showtimes("W1021", to_date=date(2026, 10, 2))

    assert showtimes.is_complete
    assert showtimes.incomplete_dates == ()


@freeze_time("2026-11-16")
def test_get_showtimes_does_not_flag_no_showtime_error_as_incomplete(allocine: Allocine, allocine_vcr):
    showtimes = allocine.get_showtimes("W0746", from_date=date(2026, 11, 16), to_date=date(2026, 11, 18))

    assert showtimes == []
    assert showtimes.is_complete
    assert showtimes.incomplete_dates == ()


def test_showtime_list_behaves_like_a_list():
    showtimes = ShowtimeList([], incomplete_errors={date(2026, 11, 16): TimeoutError("boom")})

    assert showtimes == []
    assert list(showtimes) == []
    assert showtimes.incomplete_dates == (date(2026, 11, 16),)
    assert not showtimes.is_complete


def test_get_showtimes_rejects_inverted_date_range(
    allocine,
):
    with pytest.raises(ValueError, match="from_date must be before or equal to to_date"):
        allocine.get_showtimes(
            "P0645",
            from_date=date(2026, 9, 27),
            to_date=date(2026, 9, 26),
        )


def test_get_showtimes_skips_entries_without_movie(
    allocine,
):
    assert allocine._parse_showtimes([{"movie": None, "showtimes": {}}]) == []


@freeze_time("2026-09-29")
def test_get_showtimes_ignores_null_screen_formats(allocine, json_snapshot, allocine_vcr):
    showtimes = allocine.get_showtimes(
        "P0535",
        from_date=date(2026, 9, 30),
        to_date=date(2026, 9, 30),
    )

    assert [asdict(showtime) for showtime in showtimes] == json_snapshot

    cars = next(showtime.movie for showtime in showtimes if showtime.movie.movie_id == 55774)
    assert cars.release_date == date(2006, 6, 14)
    assert asdict(cars.get_movie()) == json_snapshot(name="cars_movie")


def test_initial_theatrical_release_date_from_recorded_releases(json_snapshot):
    cassette_path = Path(__file__).parent / "cassettes" / "get_showtimes_ignores_null_screen_formats.json"
    cassette = json.loads(cassette_path.read_text())
    movies = cassette["interactions"][0]["response"]["body"]["results"]
    cars = next(item["movie"] for item in movies if item["movie"]["internalId"] == 55774)
    releases = cars["releases"]

    # Select unmodified release records from the real response, never synthetic payloads.
    without_initial = [release for release in releases if release["name"] != "Released"]
    theatrical = [release for release in releases if "THEATER" in release["releaseTags"]["tagTypes"]]
    cases = {
        "all_releases": releases,
        "reversed_releases": list(reversed(releases)),
        "theatrical_only": theatrical,
        "no_initial_release": without_initial,
        "rerelease_only": [release for release in releases if release["name"] == "ReRelease"],
        "digital_only": [release for release in releases if "ONLINE" in release["releaseTags"]["tagTypes"]],
        "physical_only": [release for release in releases if "PHYSICAL" in release["releaseTags"]["tagTypes"]],
        "no_releases": [],
    }
    assert {name: _initial_theatrical_release_date(records) for name, records in cases.items()} == json_snapshot


def test_get_theater_falls_back_to_page_metadata(allocine: Allocine, json_snapshot, allocine_vcr):
    theater = allocine.get_theater("G0FOX")

    assert asdict(theater) == json_snapshot
