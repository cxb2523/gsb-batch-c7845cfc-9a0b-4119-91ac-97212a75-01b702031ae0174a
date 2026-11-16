"""Tests for collect_pages, the paged reader collector, with faked Substack answers.

No session and no network: `ToolContext._client` returns a scripted client whose answers are local
JSON, so each test drives get_feed and search_substack through their real request and parsing code.

    .venv/Scripts/python -m pytest tests -q
    uv run python -m pytest tests -q        # the same, the way CI runs it
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest

os.environ.setdefault("SUBSTACK_MCP_HOME", tempfile.mkdtemp(prefix="substack-collect-"))
os.environ.setdefault("SUBSTACK_AUTH_TOKEN", "test-session")

import httpx
from mcp import types

from substack_api_mcp import mcp_server
from substack_api_mcp.api import create_app
from substack_api_mcp.core.context import ToolContext
from substack_api_mcp.core.errors import ErrorCode, ToolError
from substack_api_mcp.runner import Runner, all_tools
from substack_api_mcp.substack.batch import MAX_PAGES, collect_pages
from substack_api_mcp.substack.tools import get_feed as get_feed_module

CURSOR_1 = "eyJvZmZzZXQiOjIwfQ=="
CURSOR_2 = "eyJvZmZzZXQiOjQwfQ=="


# --- a scripted Substack -------------------------------------------------------------------------------


def feed_page(*posts: int, notes: tuple[int, ...] = (), cursor: str | None = None) -> dict[str, Any]:
    """A Substack reader answer: one post per ID, one Note per ID, and Substack's next cursor."""
    items: list[dict[str, Any]] = [
        {
            "type": "post",
            "post": {"id": pid, "title": f"Post {pid}", "publication_id": 9},
            "publication": {"name": "Test Pub"},
        }
        for pid in posts
    ]
    items += [
        {"type": "comment", "comment": {"id": nid, "body": f"Note {nid}", "user_id": 7, "handle": "writer"}}
        for nid in notes
    ]
    return {"items": items, "nextCursor": cursor}


def search_page(*posts: int, notes: tuple[int, ...] = (), cursor: str | None = None) -> dict[str, Any]:
    """A Substack search answer: posts, Notes and a profile block in one page."""
    page = feed_page(*posts, notes=notes, cursor=cursor)
    page["items"].append({"type": "profileSearchResults", "results": [{"id": 42, "name": "Reader", "handle": "ree"}]})
    return page


class RawAnswer:
    """The bits of a curl_cffi answer that ToolContext.call reads, so its error mapping runs for real."""

    def __init__(
        self, body: dict[str, Any] | None = None, status: int = 200, url: str = "https://substack.com/api/v1/"
    ) -> None:
        self.status_code = status
        self.url = url
        self.headers: dict[str, str] = {}
        self.text = json.dumps(body) if body is not None else ""

    def json(self) -> Any:
        return json.loads(self.text)


# How a scripted error reaches the collector: Substack's own status for the two errors the task names.
_ERROR_STATUS = {ErrorCode.RATE_LIMITED: 429, ErrorCode.AUTH_EXPIRED: 401}


class FakeSubstack:
    """Answers requests from scripted pages in order: a dict body, an ErrorCode, or an exception."""

    def __init__(self) -> None:
        self.pages: list[dict[str, Any] | ErrorCode | Exception] = []
        self.requests: list[dict[str, Any]] = []

    def script(self, *pages: dict[str, Any] | ErrorCode | Exception) -> FakeSubstack:
        self.pages = list(pages)
        return self

    def answer(self, params: dict[str, Any] | None) -> RawAnswer:
        self.requests.append(dict(params or {}))
        if not self.pages:
            raise AssertionError("collect_pages asked for more pages than the test scripted")
        page = self.pages.pop(0)
        if isinstance(page, ErrorCode):
            return RawAnswer(status=_ERROR_STATUS[page])
        if isinstance(page, Exception):
            raise page
        return RawAnswer(page)

    async def request(self, method: str, url: str, **kwargs: Any) -> RawAnswer:
        return self.answer(kwargs.get("params"))

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.requests


@pytest.fixture
def substack() -> Iterator[FakeSubstack]:
    """A fake Substack behind every tool's HTTP client."""
    fake = FakeSubstack()
    with mock.patch.object(ToolContext, "_client", lambda _ctx: fake):
        yield fake


@pytest.fixture
def runner() -> Iterator[Runner]:
    instance = Runner()
    yield instance
    asyncio.run(instance.close())


def collect(runner: Runner, args: dict[str, Any]) -> Any:
    return asyncio.run(runner.run("collect_pages", args))


# --- normal multi-page runs ----------------------------------------------------------------------------


def test_get_feed_collects_three_pages_and_appends_every_item(substack: FakeSubstack, runner: Runner) -> None:
    substack.script(feed_page(1, cursor=CURSOR_1), feed_page(2, notes=(21,), cursor=CURSOR_2), feed_page(3))

    out = collect(runner, {"tool": "get_feed", "params": {"tab": "following"}, "max_pages": 3})

    assert [post["post_id"] for post in out["posts"]] == ["1", "2", "3"]
    assert [note["note_id"] for note in out["notes"]] == ["21"]
    assert out["pages_fetched"] == 3 and out["complete"] is True
    assert out["stop_reason"] is None and out["stop_message"] is None and out["next_cursor"] is None
    assert [page["posts"] for page in out["pages"]] == [1, 1, 1]
    assert [page["cursor"] for page in out["pages"]] == [None, CURSOR_1, CURSOR_2]
    # The tool's own arguments travel page by page; the cursor comes from Substack's answer.
    assert [call.get("tab") for call in substack.calls] == ["subscribed", "subscribed", "subscribed"]
    assert [call.get("cursor") for call in substack.calls] == [None, CURSOR_1, CURSOR_2]


def test_search_substack_collects_posts_notes_and_people(substack: FakeSubstack, runner: Runner) -> None:
    substack.script(search_page(5, cursor=CURSOR_1), search_page(6, notes=(60,)))

    out = collect(runner, {"tool": "search_substack", "params": {"query": "morning"}, "max_pages": 4})

    assert [post["post_id"] for post in out["posts"]] == ["5", "6"]
    assert [note["note_id"] for note in out["notes"]] == ["60"]
    assert [person["handle"] for person in out["people"]] == ["ree", "ree"]
    assert out["pages_fetched"] == 2 and out["complete"] is True
    assert all(call.get("query") == "morning" for call in substack.calls)


def test_resume_starts_from_the_given_cursor(substack: FakeSubstack, runner: Runner) -> None:
    substack.script(feed_page(9))

    out = collect(runner, {"tool": "get_feed", "max_pages": 2, "cursor": CURSOR_2})

    assert substack.calls[0]["cursor"] == CURSOR_2
    assert [post["post_id"] for post in out["posts"]] == ["9"]
    assert out["complete"] is True


def test_a_cursor_inside_params_is_understood_too(substack: FakeSubstack, runner: Runner) -> None:
    """The cursor is part of the tool's own params, so either place works."""
    substack.script(feed_page(4))

    out = collect(runner, {"tool": "get_feed", "max_pages": 1, "params": {"tab": "for_you", "cursor": CURSOR_1}})

    assert substack.calls[0]["cursor"] == CURSOR_1
    assert [post["post_id"] for post in out["posts"]] == ["4"]
    assert out["complete"] is True


def test_reaching_max_pages_is_not_reported_as_complete(substack: FakeSubstack, runner: Runner) -> None:
    substack.script(feed_page(1, cursor=CURSOR_1), feed_page(2, cursor=CURSOR_2))

    out = collect(runner, {"tool": "get_feed", "max_pages": 2})

    assert [post["post_id"] for post in out["posts"]] == ["1", "2"]
    assert out["pages_fetched"] == 2 and out["complete"] is False
    assert out["stop_reason"] == "page_limit" and out["stop_message"]
    assert out["next_cursor"] == CURSOR_2
    assert len(substack.calls) == 2


def test_a_capped_run_resumes_from_its_own_answer(substack: FakeSubstack, runner: Runner) -> None:
    """The answer carries the params and the next_cursor, so the caller continues without guessing."""
    substack.script(feed_page(1, cursor=CURSOR_1), feed_page(2, cursor=CURSOR_2), feed_page(3))
    first = collect(runner, {"tool": "get_feed", "params": {"tab": "following"}, "max_pages": 2})
    assert first["complete"] is False and first["stop_reason"] == "page_limit"

    second = collect(
        runner, {"tool": first["tool"], "params": first["params"], "max_pages": 5, "cursor": first["next_cursor"]}
    )

    assert [post["post_id"] for post in second["posts"]] == ["3"]
    assert second["pages_fetched"] == 1 and second["complete"] is True
    # Both runs asked Substack about the same tab, and only the second one started from a cursor.
    assert [call.get("tab") for call in substack.calls] == ["subscribed"] * 3
    assert [call.get("cursor") for call in substack.calls] == [None, CURSOR_1, CURSOR_2]


# --- a page fails in the middle ------------------------------------------------------------------------


@pytest.mark.parametrize("code", [ErrorCode.RATE_LIMITED, ErrorCode.AUTH_EXPIRED])
def test_midway_error_keeps_earlier_items_and_names_the_reason(
    substack: FakeSubstack, runner: Runner, code: ErrorCode
) -> None:
    substack.script(feed_page(1, notes=(11,), cursor=CURSOR_1), code, feed_page(3))

    out = collect(runner, {"tool": "get_feed", "max_pages": 5})

    assert [post["post_id"] for post in out["posts"]] == ["1"]
    assert [note["note_id"] for note in out["notes"]] == ["11"]
    assert out["pages_fetched"] == 1
    # The failure is never disguised as a complete answer.
    assert out["complete"] is False
    assert out["stop_reason"] == code.value
    assert out["stop_message"]
    # Resume from the failed page, not the one after it, and stop asking.
    assert out["next_cursor"] == CURSOR_1
    assert len(substack.calls) == 2
    assert out["pages"][-1]["error"] == code.value


def test_an_unexpected_exception_also_stops_cleanly(substack: FakeSubstack, runner: Runner) -> None:
    substack.script(feed_page(1, cursor=CURSOR_1), RuntimeError("boom"))

    out = collect(runner, {"tool": "get_feed", "max_pages": 3})

    assert [post["post_id"] for post in out["posts"]] == ["1"]
    assert out["pages_fetched"] == 1 and out["complete"] is False
    assert out["stop_reason"] == ErrorCode.TARGET_ERROR.value
    assert out["next_cursor"] == CURSOR_1


def test_collect_pages_reports_any_exception_as_the_reason() -> None:
    """A fetcher that raises something other than ToolError still stops cleanly and keeps what it has."""

    async def fetch(args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("cursor"):
            return {"posts": [{"post_id": "1"}], "notes": [], "next_cursor": CURSOR_1}
        raise RuntimeError("boom")

    out = asyncio.run(collect_pages("get_feed", fetch, {}, 3))

    assert [post["post_id"] for post in out["posts"]] == ["1"]
    assert out["pages_fetched"] == 1 and out["complete"] is False
    assert out["stop_reason"] == ErrorCode.INTERNAL_ERROR.value
    assert "RuntimeError: boom" in (out["stop_message"] or "")
    assert out["next_cursor"] == CURSOR_1


def test_running_out_of_time_stops_before_the_next_page() -> None:
    """The collector stops when the deadline has passed, so Runner's timeout cannot drop the pages."""

    async def fetch(_args: dict[str, Any]) -> dict[str, Any]:
        return {"posts": [{"post_id": "1"}], "notes": [], "next_cursor": CURSOR_1}

    out = asyncio.run(collect_pages("get_feed", fetch, {}, 5, deadline=0.0))

    assert out["posts"] == [] and out["pages_fetched"] == 0
    assert out["complete"] is False and out["stop_reason"] == "time_limit"
    assert out["stop_message"]


def test_first_page_error_collects_nothing_and_is_not_complete(substack: FakeSubstack, runner: Runner) -> None:
    substack.script(ErrorCode.RATE_LIMITED)

    out = collect(runner, {"tool": "search_substack", "params": {"query": "x"}, "max_pages": 3})

    assert out["posts"] == [] and out["notes"] == [] and out["people"] == []
    assert out["pages_fetched"] == 0 and out["complete"] is False
    assert out["stop_reason"] == "rate_limited"
    assert out["next_cursor"] is None
    assert len(substack.calls) == 1


# --- an empty first feed -------------------------------------------------------------------------------


def test_empty_first_page_is_complete_and_empty(substack: FakeSubstack, runner: Runner) -> None:
    substack.script(feed_page())

    out = collect(runner, {"tool": "get_feed", "max_pages": 5})

    assert out["posts"] == [] and out["notes"] == []
    assert out["pages_fetched"] == 1 and out["complete"] is True
    assert out["stop_reason"] is None and out["next_cursor"] is None
    assert len(substack.calls) == 1


# --- invalid input sends no request --------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        {"tool": "list_posts", "max_pages": 2},
        {"tool": "get_feed", "max_pages": 2, "cursor": "not-a-cursor"},
        {"tool": "get_feed", "max_pages": 2, "cursor": "invalid cursor"},
        {"tool": "get_feed", "max_pages": 2, "cursor": ""},
        {"tool": "get_feed", "max_pages": 0},
        {"tool": "get_feed", "max_pages": MAX_PAGES + 1},
        {"tool": "get_feed", "max_pages": "3"},
        {"tool": "get_feed", "max_pages": True},
    ],
)
def test_invalid_input_is_refused_before_any_request(
    substack: FakeSubstack, runner: Runner, args: dict[str, Any]
) -> None:
    with pytest.raises(ToolError) as caught:
        collect(runner, args)
    assert caught.value.code is ErrorCode.INVALID_INPUT
    assert substack.calls == []


def test_params_are_checked_against_the_inner_tool_schema(substack: FakeSubstack, runner: Runner) -> None:
    with pytest.raises(ToolError) as caught:
        collect(runner, {"tool": "search_substack", "params": {}, "max_pages": 2})
    assert caught.value.code is ErrorCode.INVALID_INPUT
    assert "params" in str(caught.value) and "query" in str(caught.value)
    assert substack.calls == []


def test_each_page_goes_through_the_existing_tool(substack: FakeSubstack, runner: Runner) -> None:
    """collect_pages calls get_feed itself once per page, with the cursor it got back."""
    seen: list[dict[str, Any]] = []

    async def fake_get_feed(ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(args))
        page = len(seen)
        return {
            "tab": "for_you",
            "posts": [{"post_id": str(page)}],
            "notes": [],
            "next_cursor": None if page == 2 else CURSOR_2,
        }

    with mock.patch.object(get_feed_module, "get_feed", fake_get_feed):
        out = collect(runner, {"tool": "get_feed", "max_pages": 5})

    assert [post["post_id"] for post in out["posts"]] == ["1", "2"]
    assert out["pages_fetched"] == 2 and out["complete"] is True
    assert seen == [{}, {"cursor": CURSOR_2}]
    assert substack.calls == []  # the tool itself was faked, so no HTTP request left


# --- the entry points the task names: MCP tool and REST route -----------------------------------------


def test_registered_as_an_mcp_tool_and_a_rest_route() -> None:
    spec = all_tools()["collect_pages"]
    assert spec.access == "read"
    assert spec.route == "GET /collect"
    assert {"tool", "params", "max_pages", "cursor"} <= set(spec.input_schema["properties"])

    server = mcp_server.build(Runner())

    async def listed() -> list[types.Tool]:
        answer = await server.request_handlers[types.ListToolsRequest](types.ListToolsRequest(method="tools/list"))
        return list(answer.root.tools)  # type: ignore[union-attr]

    assert "collect_pages" in [tool.name for tool in asyncio.run(listed())]


def test_rest_route_collects_with_the_same_result(substack: FakeSubstack) -> None:
    substack.script(feed_page(1, cursor=CURSOR_1), feed_page(2))
    status, body = get_collect({"tool": "get_feed", "max_pages": 3, "params": json.dumps({"tab": "following"})})

    assert status == 200
    assert [post["post_id"] for post in body["posts"]] == ["1", "2"]
    assert body["pages_fetched"] == 2 and body["complete"] is True


def test_rest_route_answers_invalid_input_with_422(substack: FakeSubstack) -> None:
    status, body = get_collect({"tool": "get_feed", "cursor": "invalid"})
    assert status == 422
    assert body["error"] == "invalid_input"
    assert substack.calls == []


def get_collect(query: dict[str, Any]) -> Any:
    """GET the collect route through the real FastAPI app, without a server or a session."""

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(Runner()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/v1/substack/collect", params=query)

    response = asyncio.run(go())
    return response.status_code, response.json()
