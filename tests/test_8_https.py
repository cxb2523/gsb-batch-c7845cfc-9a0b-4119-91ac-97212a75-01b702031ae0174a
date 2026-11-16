"""
Offline tests for the HTTPS lookup layer (lookups/https.py)

All tests use a stubbed HTTPSConnection: no network access is performed.

Covers:
- plain 200 response
- one 302 redirect followed by 200 (final URL recorded in query info)
- relative redirect
- redirection depth exceeded (query info reflects last real request)
- missing / unresolvable Location header
- rate limiter wait recorded when triggered, absent otherwise
- site lookups (Crossref & co) still parse local fixtures correctly
- query info flows into the data dump with the "query-" prefix
"""

from json import dumps
from time import time
from typing import Dict, List, Optional, Tuple, Type

import pytest

from bibtexautocomplete.APIs.arxiv import ArxivLookup
from bibtexautocomplete.APIs.crossref import CrossrefLookup
from bibtexautocomplete.APIs.dblp import DBLPLookup
from bibtexautocomplete.APIs.inspire_hep import InpireHEPLookup
from bibtexautocomplete.APIs.openalex import OpenAlexLookup
from bibtexautocomplete.APIs.researchr import ResearchrLookup
from bibtexautocomplete.APIs.semantic_scholar import SemanticScholarLookup
from bibtexautocomplete.bibtex.entry import BibtexEntry
from bibtexautocomplete.core.data_dump import DataDump
from bibtexautocomplete.lookups.abstract_base import Data
from bibtexautocomplete.lookups.https import (
    HTTPSLookup,
    HTTPSRateCapedLookup,
    RedirectFollower,
)
from bibtexautocomplete.utils.logger import logger

logger.set_verbosity(4)


class FakeResponse:
    """Minimal stand-in for http.client.HTTPResponse"""

    def __init__(
        self,
        status: int,
        reason: str = "OK",
        headers: Optional[Dict[str, str]] = None,
        body: bytes = b"{}",
    ) -> None:
        self.status = status
        self.reason = reason
        self.headers = headers if headers is not None else {}
        self._body = body

    def getheader(self, name: str) -> Optional[str]:
        return self.headers.get(name)

    def read(self) -> bytes:
        return self._body


class FakeConnection:
    """Minimal stand-in for http.client.HTTPSConnection
    Answers from a class-level (domain, path) -> FakeResponse table
    and records every request made."""

    table: Dict[Tuple[str, str], FakeResponse] = {}
    requests: List[Tuple[str, str]] = []

    @classmethod
    def reset(cls, table: Dict[Tuple[str, str], FakeResponse]) -> None:
        cls.table = table
        cls.requests = []

    def __init__(self, domain: str, timeout: Optional[float] = None, context: object = None) -> None:
        self.domain = domain
        self.path = ""

    def request(self, method: str, path: str, body: object = None, headers: object = None) -> None:
        self.path = path
        FakeConnection.requests.append((self.domain, path))

    def getresponse(self) -> FakeResponse:
        return FakeConnection.table[(self.domain, self.path)]

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def stub_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace HTTPSConnection by our fake for all tests in this file"""
    monkeypatch.setattr("bibtexautocomplete.lookups.https.HTTPSConnection", FakeConnection)


def make_redirect_lookup(domain: str, path: str, max_depth: int = 10) -> RedirectFollower[str, Data]:
    class Lookup(RedirectFollower[str, Data]):
        silent_fail = True

        def process_data(self, data: Data) -> Data:
            return data

    Lookup.max_depth = max_depth
    lookup = Lookup("input")
    lookup.domain = domain
    lookup.path = path
    return lookup


def make_rate_caped_lookup(domain: str, path: str, query_delay: float) -> HTTPSRateCapedLookup[str, Data]:
    class Lookup(HTTPSRateCapedLookup[str, Data]):
        silent_fail = True

        def process_data(self, data: Data) -> Data:
            return data

    Lookup.query_delay = query_delay
    Lookup.last_query_time = 0.0
    lookup = Lookup("input")
    lookup.domain = domain
    lookup.path = path
    return lookup


class TestPlainQuery:
    def test_200(self) -> None:
        FakeConnection.reset({("example.com", "/foo"): FakeResponse(200)})
        lookup = make_redirect_lookup("example.com", "/foo")
        assert lookup.query() is not None
        info = lookup.get_last_query_info()
        assert info["url"] == "https://example.com/foo"
        assert info["response-status"] == 200
        assert isinstance(info["response-time"], float)
        assert FakeConnection.requests == [("example.com", "/foo")]

    def test_info_keys_unchanged(self) -> None:
        """No redirect, no rate cap: exactly the historical three keys"""
        FakeConnection.reset({("example.com", "/foo"): FakeResponse(200)})
        lookup = make_redirect_lookup("example.com", "/foo")
        lookup.query()
        info = lookup.get_last_query_info()
        assert set(info.keys()) == {"url", "response-time", "response-status"}


class TestRedirects:
    def test_302_then_200_records_final_url(self) -> None:
        FakeConnection.reset(
            {
                ("example.com", "/foo"): FakeResponse(302, "Found", {"Location": "https://other.org/bar?x=1"}),
                ("other.org", "/bar?x=1"): FakeResponse(200),
            }
        )
        lookup = make_redirect_lookup("example.com", "/foo")
        assert lookup.query() is not None
        info = lookup.get_last_query_info()
        # url is the final actually requested address
        assert info["url"] == "https://other.org/bar?x=1"
        assert info["response-status"] == 200
        assert FakeConnection.requests == [("example.com", "/foo"), ("other.org", "/bar?x=1")]

    def test_relative_redirect(self) -> None:
        FakeConnection.reset(
            {
                ("example.com", "/foo"): FakeResponse(301, "Moved", {"Location": "/bar"}),
                ("example.com", "/bar"): FakeResponse(200),
            }
        )
        lookup = make_redirect_lookup("example.com", "/foo")
        assert lookup.query() is not None
        info = lookup.get_last_query_info()
        assert info["url"] == "https://example.com/bar"
        assert info["response-status"] == 200

    def test_depth_exceeded_records_last_real_request(self) -> None:
        table = {}
        for i in range(3):
            table[("example.com", f"/r{i}")] = FakeResponse(302, "Found", {"Location": f"https://example.com/r{i + 1}"})
        FakeConnection.reset(table)
        lookup = make_redirect_lookup("example.com", "/r0", max_depth=3)
        assert lookup.query() is None
        info = lookup.get_last_query_info()
        # last request actually made was /r2 (depth 3 >= max_depth stops the chain)
        assert FakeConnection.requests == [
            ("example.com", "/r0"),
            ("example.com", "/r1"),
            ("example.com", "/r2"),
        ]
        assert info["url"] == "https://example.com/r2"
        assert info["response-status"] == 302

    def test_missing_location_header(self) -> None:
        FakeConnection.reset({("example.com", "/foo"): FakeResponse(302, "Found")})
        lookup = make_redirect_lookup("example.com", "/foo")
        result = lookup.query()
        assert result is not None
        assert result.code == 302
        info = lookup.get_last_query_info()
        assert info["url"] == "https://example.com/foo"
        assert info["response-status"] == 302

    def test_unresolvable_location(self) -> None:
        FakeConnection.reset({("example.com", "/foo"): FakeResponse(302, "Found", {"Location": "http://"})})
        lookup = make_redirect_lookup("example.com", "/foo")
        result = lookup.query()
        assert result is not None
        assert result.code == 302
        info = lookup.get_last_query_info()
        assert info["url"] == "https://example.com/foo"
        assert info["response-status"] == 302

    def test_final_request_failure_still_records_final_url(self) -> None:
        """If the last request of a redirect chain fails, the query info
        still reports the final URL that was requested"""

        class FailingConnection(FakeConnection):
            def getresponse(self) -> FakeResponse:
                if self.domain == "other.org":
                    raise OSError("connection reset")
                return super().getresponse()

        FakeConnection.reset(
            {
                ("example.com", "/foo"): FakeResponse(302, "Found", {"Location": "https://other.org/bar"}),
            }
        )
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr("bibtexautocomplete.lookups.https.HTTPSConnection", FailingConnection)
            lookup = make_redirect_lookup("example.com", "/foo")
            assert lookup.query() is None
            info = lookup.get_last_query_info()
        assert info["url"] == "https://other.org/bar"


class TestRateCap:
    def test_wait_recorded_when_triggered(self) -> None:
        FakeConnection.reset({("example.com", "/foo"): FakeResponse(200)})
        lookup = make_rate_caped_lookup("example.com", "/foo", query_delay=0.2)
        # simulate a query that just happened: forces the rate limiter to wait
        type(lookup).last_query_time = time()
        assert lookup.query() is not None
        info = lookup.get_last_query_info()
        assert "rate-limit-wait" in info
        wait = info["rate-limit-wait"]
        assert isinstance(wait, float)
        assert 0.0 < wait <= 0.2
        # historical keys untouched
        assert info["url"] == "https://example.com/foo"
        assert info["response-status"] == 200
        assert isinstance(info["response-time"], float)

    def test_no_wait_no_key(self) -> None:
        FakeConnection.reset({("example.com", "/foo"): FakeResponse(200)})
        lookup = make_rate_caped_lookup("example.com", "/foo", query_delay=0.0)
        type(lookup).last_query_time = 0.0
        assert lookup.query() is not None
        info = lookup.get_last_query_info()
        assert "rate-limit-wait" not in info
        assert set(info.keys()) == {"url", "response-time", "response-status"}

    def test_wait_key_is_stable(self) -> None:
        assert HTTPSRateCapedLookup.rate_limit_wait_key == "rate-limit-wait"


# ================= Site lookups: parsing unaffected =================

entry_crossref = {
    "title": "Reactive Path Deformation for Nonholonomic Mobile Robots",
    "author": "Lamiraux and Bonnafous",
    "ID": "Lam",
}

CROSSREF_JSON = {
    "status": "ok",
    "message": {
        "DOI": "10.1109/tro.2004.829459",
        "title": ["Reactive Path Deformation for Nonholonomic Mobile Robots"],
        "container-title": ["IEEE Transactions on Robotics"],
        "type": "journal-article",
        "author": [
            {"family": "Lamiraux", "given": "Florent"},
            {"family": "Bonnafous", "given": "David"},
        ],
        "published-print": {"date-parts": [[2004, 10]]},
        "publisher": "IEEE",
        "volume": "20",
        "page": "967-977",
        "ISSN": ["1552-3098"],
    },
}


class TestSiteLookupsParsing:
    def test_crossref_parsing(self) -> None:
        """Crossref results parsed from a local fixture are unchanged"""
        bib = BibtexEntry.from_entry("test", entry_crossref)
        lookup = CrossrefLookup(bib)
        lookup.doi = "10.1109/tro.2004.829459"  # set by DAT_Query_Mixin during query
        data = Data(data=dumps(CROSSREF_JSON).encode(), code=200, reason="OK", delay=0.0)
        result = lookup.process_data(data)
        assert result is not None
        assert result.doi.to_str() == "10.1109/tro.2004.829459"
        assert result.title.to_str() == "Reactive Path Deformation for Nonholonomic Mobile Robots"
        assert result.journal.to_str() == "IEEE Transactions on Robotics"
        assert result.publisher.to_str() == "IEEE"
        assert result.year.to_str() == "2004"
        assert result.volume.to_str() == "20"
        assert result.pages.to_str() is not None

    def test_crossref_bad_code_gives_none(self) -> None:
        bib = BibtexEntry.from_entry("test", entry_crossref)
        lookup = CrossrefLookup(bib)
        lookup.doi = "10.1109/tro.2004.829459"
        data = Data(data=b"not found", code=404, reason="Not Found", delay=0.0)
        assert lookup.process_data(data) is None

    @pytest.mark.parametrize(
        "lookup_class",
        [
            ArxivLookup,
            CrossrefLookup,
            DBLPLookup,
            InpireHEPLookup,
            OpenAlexLookup,
            ResearchrLookup,
            SemanticScholarLookup,
        ],
    )
    def test_lookups_construct_and_info_is_dict(
        self, lookup_class: Type[HTTPSLookup[BibtexEntry, BibtexEntry]]
    ) -> None:
        bib = BibtexEntry.from_entry("test", entry_crossref)
        lookup = lookup_class(bib)
        info = lookup.get_last_query_info()
        assert isinstance(info, dict)


class TestDataDump:
    def test_query_info_flows_into_dump(self) -> None:
        """The dump chain prefixes query info keys with 'query-'"""
        dump = DataDump("entry_id")
        bib = BibtexEntry.from_entry("test", entry_crossref)
        dump.add_entry(
            "test_lookup",
            bib,
            {
                "url": "https://other.org/bar?x=1",
                "response-status": 200,
                "response-time": 0.123,
                "rate-limit-wait": 0.456,
            },
        )
        result = dump.to_dict()["test_lookup"]
        assert result is not None
        assert result["query-url"] == "https://other.org/bar?x=1"
        assert result["query-response-status"] == 200
        assert result["query-response-time"] == 0.123
        assert result["query-rate-limit-wait"] == 0.456
