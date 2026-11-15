from typing import Optional

from pytest import raises

from faapi import FAAPI
from faapi import FavoritesResult


def make_api(pages: dict[str, tuple[list, Optional[str]]]) -> FAAPI:
    """
    Build a FAAPI instance without network access, with FAAPI.favorites stubbed to serve the given pages.
    """
    api: FAAPI = FAAPI.__new__(FAAPI)
    requested: list[str] = []

    def favorites(user: str, page: str = "") -> tuple[list, Optional[str]]:
        requested.append(page)
        return pages[page]

    api.favorites = favorites  # type: ignore[method-assign]
    api._requested_pages = requested  # type: ignore[attr-defined]
    return api


def test_favorites_all_complete():
    api: FAAPI = make_api({
        "": (["s1", "s2"], "123"),
        "123": (["s3"], None),
    })

    result: FavoritesResult = api.favorites_all("user")

    assert isinstance(result, FavoritesResult)
    assert result.submissions == ["s1", "s2", "s3"]
    assert result.complete
    assert api._requested_pages == ["", "123"]


def test_favorites_all_start_page():
    api: FAAPI = make_api({
        "456": (["s1"], None),
    })

    result: FavoritesResult = api.favorites_all("user", "456")

    assert result.submissions == ["s1"]
    assert result.complete
    assert api._requested_pages == ["456"]


def test_favorites_all_empty_page_stops():
    api: FAAPI = make_api({
        "": (["s1"], "5"),
        "5": ([], "9"),
    })

    result: FavoritesResult = api.favorites_all("user")

    assert result.submissions == ["s1"]
    assert not result.complete
    assert api._requested_pages == ["", "5"]  # "9" is never requested


def test_favorites_all_next_page_equal_to_current_stops():
    api: FAAPI = make_api({
        "": (["s1"], "7"),
        "7": (["s2"], "7"),
    })

    result: FavoritesResult = api.favorites_all("user")

    assert result.submissions == ["s1", "s2"]
    assert not result.complete
    assert api._requested_pages == ["", "7"]


def test_favorites_all_repeated_page_stops():
    api: FAAPI = make_api({
        "": (["s1"], "7"),
        "7": (["s2"], ""),
    })

    result: FavoritesResult = api.favorites_all("user")

    assert result.submissions == ["s1", "s2"]
    assert not result.complete
    assert api._requested_pages == ["", "7"]


def test_favorites_all_exception_carries_partial_results():
    api: FAAPI = make_api({
        "": (["s1", "s2"], "3"),
    })

    def favorites(user: str, page: str = "") -> tuple[list, Optional[str]]:
        if page == "3":
            raise ConnectionError("boom")
        return ["s1", "s2"], "3"

    api.favorites = favorites  # type: ignore[method-assign]

    with raises(ConnectionError) as err_info:
        api.favorites_all("user")

    assert err_info.value.partial_results == ["s1", "s2"]
