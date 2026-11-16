import json
from pathlib import Path

import pytest

from engines.base_engine import BaseEngine
from models.league_section_status import LeagueSectionState
from parsers.base_parser import BaseParser
from scraper.scraper_service import ScraperService
from utils.json_formatter import JsonFormatter
from utils.url_builder import LEAGUE_SECTIONS, FlashscoreUrlBuilder

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "football"
LEAGUE_URL = "https://www.flashscore.com/football/germany/bundesliga/"
RESULTS_URL = "https://www.flashscore.com/football/germany/bundesliga/results/"
FIXTURES_URL = "https://www.flashscore.com/football/germany/bundesliga/fixtures/"

EMPTY_PAGE = '<div class="sportName soccer"></div>'


class FakeEngine(BaseEngine):
    def __init__(self, pages: dict[str, str]) -> None:
        super().__init__()
        self.pages = pages
        self.load_more: dict[str, str | None] = {}

    def _get_page(self, url: str, wait_for_selector: str | None = None, load_more_selector: str | None = None) -> str:
        self.load_more[url] = load_more_selector
        if url not in self.pages:
            raise TimeoutError(f"Timeout waiting for {wait_for_selector}")
        return self.pages[url]

    def _get_pages(self, urls: dict[str, str], wait_for_selectors: dict[str, str | None] | None = None) -> dict[str, str]:
        return {key: self._get_page(url) for key, url in urls.items()}

    def _close(self) -> None:
        pass


def _read_fixture(name: str) -> str:
    try:
        return (FIXTURES_DIR / name).read_text(encoding="utf-8")
    except FileNotFoundError:
        pytest.fail(f"Fixture file not found: {FIXTURES_DIR / name}")


def _match_row(event_url: str, home: str, away: str, scheduled: bool = False) -> str:
    classes = "event__match event__match--oneLine"
    if scheduled:
        classes += " event__match--scheduled"
        score = ""
    else:
        score = '<div class="event__score event__score--home">1</div><div class="event__score event__score--away">0</div>'
    return f"""
    <div class="{classes}">
      <div class="event__stageTime">01.09. 20:30</div>
      <div class="event__participant event__homeParticipant">{home}</div>
      <a class="eventRowLink" href="{event_url}"></a>
      {score}
      <div class="event__participant event__awayParticipant">{away}</div>
    </div>"""


def _league_page(*rows: str) -> str:
    return '<div class="sportName soccer">' + "".join(rows) + "</div>"


def _service(sport: str, pages: dict[str, str]) -> tuple[ScraperService, FakeEngine]:
    engine = FakeEngine(pages)
    return ScraperService(engine, BaseParser.create(sport)), engine


def _status_by_section(result: dict) -> dict[str, object]:
    return {status.section: status for status in result["section_statuses"]}


class TestBuildLeagueUrl:
    @pytest.mark.parametrize(
        "league",
        [
            "https://www.flashscore.com/football/germany/bundesliga/",
            "https://www.flashscore.com/football/germany/bundesliga/results/",
            "https://www.flashscore.com/football/germany/bundesliga/fixtures/?foo=bar",
        ],
    )
    def test_normalizes_league_url(self, league: str) -> None:
        assert FlashscoreUrlBuilder.build_league_url("football", league, "results") == RESULTS_URL


class TestFetchAndParseLeague:
    def test_merges_results_and_fixtures(self) -> None:
        service, engine = _service("football", {RESULTS_URL: _read_fixture("league_results.html"), FIXTURES_URL: _read_fixture("league_fixtures.html")})

        result = service.fetch_and_parse_league(LEAGUE_URL)

        statuses = {match.status for match in result["matches"]}
        assert statuses == {"finished", "scheduled"}
        urls = [match.event_url for match in result["matches"]]
        assert len(urls) == len(set(urls))
        assert all(selector is not None for selector in engine.load_more.values())

        section_statuses = result["section_statuses"]
        assert [status.section for status in section_statuses] == list(LEAGUE_SECTIONS)
        by_section = _status_by_section(result)
        assert by_section["results"].status == LeagueSectionState.OK
        assert by_section["fixtures"].status == LeagueSectionState.OK
        assert by_section["results"].matches_count == 3
        assert by_section["fixtures"].matches_count == 2
        assert all(status.url for status in section_statuses)
        # Every final match is attributed to exactly one section.
        assert sum(status.matches_count for status in section_statuses) == len(result["matches"])

    def test_missing_section_is_skipped(self) -> None:
        service, _ = _service("football", {RESULTS_URL: _read_fixture("league_results.html")})

        result = service.fetch_and_parse_league(LEAGUE_URL)

        assert "error" not in result
        assert {match.status for match in result["matches"]} == {"finished"}
        assert len(result["matches"]) == 3

        by_section = _status_by_section(result)
        assert by_section["results"].status == LeagueSectionState.OK
        assert by_section["results"].matches_count == 3
        assert by_section["fixtures"].status == LeagueSectionState.FAILED
        assert by_section["fixtures"].matches_count == 0
        assert by_section["fixtures"].url == FIXTURES_URL

    def test_all_sections_failing_returns_error(self) -> None:
        service, _ = _service("football", {})

        result = service.fetch_and_parse_league(LEAGUE_URL)

        assert result["error"]
        assert result["url"] == RESULTS_URL
        assert "section_statuses" not in result
        assert "matches" not in result

    def test_duplicate_event_urls_are_deduplicated_keeping_first_occurrence(self) -> None:
        shared_url = "https://example.test/match/shared"
        pages = {
            RESULTS_URL: _league_page(
                _match_row(shared_url, "Home First", "Away First"),
                _match_row("https://example.test/match/results-only", "Results Two", "Away Two"),
            ),
            FIXTURES_URL: _league_page(
                _match_row(shared_url, "Home Duplicate", "Away Duplicate", scheduled=True),
                _match_row("https://example.test/match/fixtures-only", "Fixture Two", "Away Two", scheduled=True),
            ),
        }
        service, _ = _service("football", pages)

        result = service.fetch_and_parse_league(LEAGUE_URL)

        matches_by_url = {match.event_url: match for match in result["matches"]}
        assert set(matches_by_url) == {
            shared_url,
            "https://example.test/match/results-only",
            "https://example.test/match/fixtures-only",
        }
        # The record from the first section (results) wins over the duplicate in fixtures.
        first = matches_by_url[shared_url]
        assert first.home == "Home First"
        assert first.status == "finished"

        by_section = _status_by_section(result)
        assert by_section["results"].status == LeagueSectionState.OK
        assert by_section["results"].matches_count == 2
        # The duplicate row is not counted again for the fixtures section.
        assert by_section["fixtures"].status == LeagueSectionState.OK
        assert by_section["fixtures"].matches_count == 1
        assert sum(status.matches_count for status in result["section_statuses"]) == len(result["matches"])

    def test_section_whose_matches_all_deduplicate_is_reported_empty(self) -> None:
        shared_url = "https://example.test/match/shared"
        pages = {
            RESULTS_URL: _league_page(_match_row(shared_url, "Home First", "Away First")),
            FIXTURES_URL: _league_page(_match_row(shared_url, "Home Duplicate", "Away Duplicate", scheduled=True)),
        }
        service, _ = _service("football", pages)

        result = service.fetch_and_parse_league(LEAGUE_URL)

        assert len(result["matches"]) == 1
        by_section = _status_by_section(result)
        assert by_section["results"].status == LeagueSectionState.OK
        assert by_section["results"].matches_count == 1
        # Fetched and parsed fine, but every row was dropped by de-duplication.
        assert by_section["fixtures"].status == LeagueSectionState.EMPTY
        assert by_section["fixtures"].matches_count == 0

    def test_section_without_any_matches_is_reported_empty(self) -> None:
        pages = {
            RESULTS_URL: _league_page(_match_row("https://example.test/match/results-only", "Home", "Away")),
            FIXTURES_URL: EMPTY_PAGE,
        }
        service, _ = _service("football", pages)

        result = service.fetch_and_parse_league(LEAGUE_URL)

        assert len(result["matches"]) == 1
        by_section = _status_by_section(result)
        assert by_section["results"].status == LeagueSectionState.OK
        assert by_section["fixtures"].status == LeagueSectionState.EMPTY
        assert by_section["fixtures"].matches_count == 0

    def test_empty_results_section_keeps_fixtures_matches(self) -> None:
        pages = {
            RESULTS_URL: EMPTY_PAGE,
            FIXTURES_URL: _league_page(
                _match_row("https://example.test/match/fixtures-only", "Home", "Away", scheduled=True)
            ),
        }
        service, _ = _service("football", pages)

        result = service.fetch_and_parse_league(LEAGUE_URL)

        assert "error" not in result
        assert [match.event_url for match in result["matches"]] == ["https://example.test/match/fixtures-only"]
        by_section = _status_by_section(result)
        assert by_section["results"].status == LeagueSectionState.EMPTY
        assert by_section["fixtures"].status == LeagueSectionState.OK
        assert by_section["fixtures"].matches_count == 1

    def test_league_response_remains_json_serializable_for_existing_callers(self) -> None:
        service, _ = _service("football", {RESULTS_URL: _read_fixture("league_results.html")})

        payload = json.loads(JsonFormatter.to_json(service.fetch_and_parse_league(LEAGUE_URL)))

        # Legacy keys keep their shape.
        assert payload["league_url"] == LEAGUE_URL
        assert isinstance(payload["matches"], list) and payload["matches"]
        assert all({"event_url", "status", "round", "date", "home", "away"} <= set(match) for match in payload["matches"])

        # New per-section field carries plain JSON values.
        assert isinstance(payload["section_statuses"], list)
        assert [status["section"] for status in payload["section_statuses"]] == list(LEAGUE_SECTIONS)
        for status in payload["section_statuses"]:
            assert set(status) == {"section", "url", "status", "matches_count"}
            assert status["status"] in {state.value for state in LeagueSectionState}
            assert isinstance(status["matches_count"], int)

    def test_error_response_remains_json_serializable(self) -> None:
        service, _ = _service("football", {})

        payload = json.loads(JsonFormatter.to_json(service.fetch_and_parse_league(LEAGUE_URL)))

        assert payload["error"]
        assert payload["url"] == RESULTS_URL
        assert "section_statuses" not in payload

    def test_unsupported_sport_raises(self) -> None:
        service, _ = _service("volleyball", {})

        with pytest.raises(ValueError, match="not supported"):
            service.fetch_and_parse_league("https://www.flashscore.com/volleyball/poland/plusliga/")
