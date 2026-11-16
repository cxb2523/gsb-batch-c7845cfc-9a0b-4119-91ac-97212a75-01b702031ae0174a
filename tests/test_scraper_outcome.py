"""Tests for the three-state scrape outcome (OK / EMPTY / FAILED).

Rule under test: a structurally valid response from ANY path (API serial
query, API account query, or HTML fallback) — including an empty list —
means the query succeeded. Only when EVERY path fails (network error,
non-200 status, unparseable response) is the scrape FAILED.
"""

import aiohttp

from app.scraper.kplc import (
    ScrapeStatus,
    SOURCE_API_SERIAL,
    SOURCE_API_ACCOUNT,
    SOURCE_HTML_FALLBACK,
    scrape_meter_tokens,
)

from conftest import (
    FakeResponse,
    FakeSession,
    contract_json,
    make_handler,
    network_error,
    EMPTY_CONTRACT_JSON,
    HTML_WITH_TABLE,
    HTML_EMPTY_RESULTS,
    TOKEN_1,
    TOKEN_2,
)


async def test_ok_with_records_via_serial_query():
    session = FakeSession(make_handler(
        api_response=lambda url: FakeResponse(
            200,
            json_data=contract_json([TOKEN_1, TOKEN_2])
            if "serialNumberMeter" in url else EMPTY_CONTRACT_JSON,
        ),
    ))
    result = await scrape_meter_tokens("12345678901", session=session)

    assert result.status == ScrapeStatus.OK
    assert result.success is True
    assert result.error is None
    assert [t.token_number for t in result.tokens] == [TOKEN_1, TOKEN_2]
    assert all(t.source == SOURCE_API_SERIAL for t in result.tokens)
    assert result.tariff == "Domestic"
    assert result.account_reference == "ACC-001"


async def test_empty_when_api_returns_valid_empty_list():
    """HTTP 200 with an empty data list = query succeeded, zero records."""
    session = FakeSession(make_handler(
        api_response=FakeResponse(200, json_data=EMPTY_CONTRACT_JSON),
        html_response=FakeResponse(404, text_data="not found"),
    ))
    result = await scrape_meter_tokens("12345678901", session=session)

    assert result.status == ScrapeStatus.EMPTY
    assert result.success is True
    assert result.error is None
    assert result.tokens == []


async def test_empty_when_html_fallback_returns_valid_empty_page():
    """API unreachable, but HTML fallback returned a valid empty results page."""
    session = FakeSession(make_handler(
        token_ok=False,
        html_response=FakeResponse(200, text_data=HTML_EMPTY_RESULTS),
    ))
    result = await scrape_meter_tokens("12345678901", session=session)

    assert result.status == ScrapeStatus.EMPTY
    assert result.success is True
    assert result.error is None
    assert result.tokens == []


async def test_failed_when_all_paths_fail_with_network_errors():
    session = FakeSession(network_error)
    result = await scrape_meter_tokens("12345678901", session=session)

    assert result.status == ScrapeStatus.FAILED
    assert result.success is False
    assert result.error  # human-readable reason present
    assert result.tokens == []


async def test_failed_when_non_200_everywhere():
    session = FakeSession(make_handler(
        token_ok=True,
        api_response=FakeResponse(500, text_data="internal server error"),
        html_response=FakeResponse(503, text_data="service unavailable"),
    ))
    result = await scrape_meter_tokens("12345678901", account_number="ACC-9",
                                       session=session)

    assert result.status == ScrapeStatus.FAILED
    assert result.success is False
    assert "HTTP 500" in result.error
    assert SOURCE_HTML_FALLBACK in result.error


async def test_failed_when_response_unparseable():
    """HTTP 200 with a body that is not valid JSON is a failed path."""
    session = FakeSession(make_handler(
        api_response=FakeResponse(200, json_exc=ValueError("Expecting value")),
        html_response=FakeResponse(404, text_data="not found"),
    ))
    result = await scrape_meter_tokens("12345678901", session=session)

    assert result.status == ScrapeStatus.FAILED
    assert result.success is False
    assert "unparseable" in result.error


async def test_failed_when_bearer_token_unobtainable_and_html_down():
    session = FakeSession(make_handler(
        token_ok=False,
        html_response=FakeResponse(enter_exc=aiohttp.ClientError("boom")),
    ))
    result = await scrape_meter_tokens("12345678901", session=session)

    assert result.status == ScrapeStatus.FAILED
    assert "bearer token" in result.error
    assert SOURCE_HTML_FALLBACK in result.error


async def test_html_fallback_succeeds_when_api_fails():
    """API path dead, HTML fallback returns a results table -> OK."""
    session = FakeSession(make_handler(
        token_ok=False,
        html_response=lambda url: (
            FakeResponse(200, text_data=HTML_WITH_TABLE)
            if "?meter=" in url else FakeResponse(404, text_data="not found")
        ),
    ))
    result = await scrape_meter_tokens("12345678901", session=session)

    assert result.status == ScrapeStatus.OK
    assert result.success is True
    assert len(result.tokens) == 1
    assert result.tokens[0].token_number == TOKEN_1
    assert result.tokens[0].source == SOURCE_HTML_FALLBACK


async def test_account_query_path_source_and_cross_path_dedup():
    """Token found via accountReference query is stamped api_account;
    the same token returned by both paths yields a single record."""
    def api(url):
        if "serialNumberMeter" in url:
            return FakeResponse(200, json_data=contract_json([TOKEN_1], account_ref="ACC-1"))
        # accountReference query returns one duplicate + one new token
        return FakeResponse(200, json_data=contract_json([TOKEN_1, TOKEN_2], account_ref="ACC-1"))

    session = FakeSession(make_handler(api_response=api))
    result = await scrape_meter_tokens("12345678901", session=session)

    assert result.status == ScrapeStatus.OK
    by_number = {t.token_number: t for t in result.tokens}
    assert set(by_number) == {TOKEN_1, TOKEN_2}
    # First occurrence (serial query) wins for the duplicate
    assert by_number[TOKEN_1].source == SOURCE_API_SERIAL
    assert by_number[TOKEN_2].source == SOURCE_API_ACCOUNT
