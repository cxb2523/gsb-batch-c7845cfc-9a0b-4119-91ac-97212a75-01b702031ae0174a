"""Shared test fixtures: a fake aiohttp session serving canned KPLC responses.

No test talks to the real KPLC portal — every response is a local sample.
"""

import aiohttp
import pytest

from app.scraper import kplc


# ---------------------------------------------------------------------------
# Fake aiohttp session
# ---------------------------------------------------------------------------

class FakeResponse:
    """Mimics the aiohttp response context manager used by the scraper."""

    def __init__(self, status=200, json_data=None, text_data="",
                 json_exc=None, enter_exc=None):
        self.status = status
        self._json_data = json_data
        self._text_data = text_data
        self._json_exc = json_exc
        self._enter_exc = enter_exc

    async def __aenter__(self):
        if self._enter_exc is not None:
            raise self._enter_exc
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data

    async def text(self):
        return self._text_data


class FakeSession:
    """Drop-in for aiohttp.ClientSession; routes requests to a handler."""

    def __init__(self, handler):
        self._handler = handler
        self.requests: list[tuple[str, str]] = []

    def get(self, url, **kwargs):
        self.requests.append(("GET", url))
        return self._handler("GET", url, **kwargs)

    def post(self, url, **kwargs):
        self.requests.append(("POST", url))
        return self._handler("POST", url, **kwargs)

    async def close(self):
        pass


# ---------------------------------------------------------------------------
# Canned KPLC payloads
# ---------------------------------------------------------------------------

TOKEN_1 = "11111111111111111111"
TOKEN_2 = "22222222222222222222"
TOKEN_3 = "33333333333333333333"


def contract_json(token_numbers, account_ref="ACC-001"):
    """A structurally valid newContractList response body."""
    return {
        "data": [
            {
                "accountReference": account_ref,
                "colPrepayment": [
                    {
                        "tokenNo": t,
                        "trnTimestamp": 1_730_000_000_000,
                        "trnUnits": 12.5,
                        "trnAmount": 300.0,
                        "pMethod": "M-PESA",
                        "tariff": "Domestic",
                    }
                    for t in token_numbers
                ],
            }
        ]
    }


EMPTY_CONTRACT_JSON = {"data": []}

HTML_WITH_TABLE = """
<html><body>
<h2>Token Purchase History</h2>
<table>
  <tr><th>Token No</th><th>Units</th><th>Amount</th><th>Date</th></tr>
  <tr><td>1111 1111 1111 1111 1111</td><td>15.3</td><td>500</td><td>01/11/2026</td></tr>
</table>
</body></html>
"""

# A valid results page that simply contains no purchase records
HTML_EMPTY_RESULTS = """
<html><body>
<h2>Token Purchase History</h2>
<p>No token purchase records found for this meter.</p>
</body></html>
"""


def make_handler(api_response=None, html_response=None,
                 token_ok=True, config_ok=True):
    """Build a FakeSession handler covering all KPLC endpoints the scraper hits.

    api_response / html_response may be a FakeResponse or a callable(url) ->
    FakeResponse (useful when serial and account queries must differ).
    """
    def _pick(resp, url):
        return resp(url) if callable(resp) else resp

    def handler(method, url, **kwargs):
        if "appConfig.json" in url:
            if config_ok:
                return FakeResponse(200, json_data={"data": {"publicScope": "x"}})
            return FakeResponse(500, text_data="config down")
        if method == "POST" and url.rstrip("/").endswith("/token"):
            if token_ok:
                return FakeResponse(200, json_data={"access_token": "fake-bearer"})
            return FakeResponse(500, text_data="token endpoint down")
        if "newContractList" in url:
            return _pick(api_response, url)
        # Everything else is an HTML fallback URL
        if html_response is not None:
            return _pick(html_response, url)
        return FakeResponse(404, text_data="not found")

    return handler


def network_error(_method, url, **kwargs):
    """Handler where every request fails at the network level."""
    return FakeResponse(enter_exc=aiohttp.ClientError("connection refused"))


@pytest.fixture(autouse=True)
def reset_kplc_scope_cache():
    """The scraper caches publicScope in a module global — isolate tests."""
    kplc._cached_scope = None
    yield
    kplc._cached_scope = None
