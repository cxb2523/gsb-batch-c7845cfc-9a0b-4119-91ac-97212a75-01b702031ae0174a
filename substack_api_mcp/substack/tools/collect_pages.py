"""Collect pages."""

from __future__ import annotations

import importlib
import time
from typing import TYPE_CHECKING, Any

import jsonschema

from substack_api_mcp.core.errors import ErrorCode, ToolError
from substack_api_mcp.core.tools import ToolSpec, tool
from substack_api_mcp.substack.batch import MAX_PAGES, PAGED_TOOLS, page_limit, start_cursor
from substack_api_mcp.substack.batch import collect_pages as gather

if TYPE_CHECKING:
    from substack_api_mcp.core.context import ToolContext

DEFAULT_PAGES = 10
# The call's own time budget; the collector stops starting pages before it ends (see RESERVE_S).
TIMEOUT_S = 300
# Seconds kept back from the timeout for the answer itself and one page still on the wire (30s).
RESERVE_S = 45

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool": {
            "type": "string",
            "enum": list(PAGED_TOOLS),
            "description": "get_feed (your home feed) or search_substack (all of Substack's search).",
        },
        "params": {
            "type": "object",
            "description": 'The tool\'s own arguments, unchanged: {"tab": "following"} or {"query": "morning"}.',
        },
        "max_pages": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_PAGES,
            "description": f"How many pages to fetch at most, 1 to {MAX_PAGES}. Default: {DEFAULT_PAGES}.",
        },
        "cursor": {
            "type": "string",
            "description": "Optional next_cursor from an earlier collect_pages, to resume where it stopped.",
        },
    },
    "required": ["tool"],
    "additionalProperties": False,
}

EXAMPLE_RESPONSE: dict[str, Any] = {
    "tool": "search_substack",
    "params": {"query": "morning routine"},
    "posts": [
        {
            "post_id": "197396217",
            "title": "A Guide to Your Morning Routine",
            "publication": "Brain Health Decoded",
            "publication_id": "5262902",
            "published_at": "2026-05-12T19:25:47.700Z",
            "likes": 410,
            "url": "https://example.substack.com/p/a-guide-to-your-morning-routine",
        }
    ],
    "notes": [
        {
            "note_id": "322981144",
            "author": {"user_id": "18270011", "name": "Laura Mills", "handle": "lauramills"},
            "body": "Morning light beats any supplement.",
            "published_at": "2026-09-02T07:11:00.000Z",
            "likes": 88,
        }
    ],
    "people": [],
    "pages_fetched": 2,
    "complete": False,
    "stop_reason": "rate_limited",
    "stop_message": "Substack is limiting this account; try again in a few minutes.",
    "next_cursor": "eyJvZmZzZXQiOjIwfQ==",
    "pages": [
        {"page": 1, "cursor": None, "posts": 1, "notes": 1, "people": 0},
        {
            "page": 2,
            "cursor": "eyJvZmZzZXQiOjIwfQ==",
            "error": "rate_limited",
            "message": "Substack is limiting this account; try again in a few minutes.",
        },
    ],
}


@tool(
    name="collect_pages",
    title="Collect pages",
    access="read",
    confirm=False,
    description="Collect many pages of your feed or a Substack search in one call, keeping the pages that worked.",
    details=(
        "Calls get_feed or search_substack page after page, with that tool's own params, up to max_pages pages, "
        "and adds the posts and Notes of every page to one answer (a search also adds its people). When a page "
        "fails (rate_limited, auth_expired, timeout), it stops there, keeps what it already has and says why in "
        "stop_reason and stop_message. next_cursor is the cursor of that failed page, so pass it back as cursor "
        "to resume. A run that reaches max_pages while Substack has more stops with stop_reason page_limit. "
        "complete is true only when Substack had no next page, so a short answer never reads as the whole list. "
        "pages holds one line per page: how many items it added, or the error that stopped it. A call that runs "
        "out of time stops the same way with stop_reason time_limit. params is echoed so a saved answer can be "
        "resumed as it is."
    ),
    input_schema=INPUT_SCHEMA,
    route="GET /collect",
    example_response=EXAMPLE_RESPONSE,
    timeout_s=TIMEOUT_S,
)
async def collect_pages(ctx: ToolContext, args: dict[str, Any]) -> Any:

    name = args.get("tool")
    if name not in PAGED_TOOLS:
        raise ToolError(f"tool must be one of: {', '.join(PAGED_TOOLS)}.", ErrorCode.INVALID_INPUT)
    limit = page_limit(args.get("max_pages", DEFAULT_PAGES))
    raw_params = args.get("params") or {}
    if not isinstance(raw_params, dict):
        raise ToolError("params must be a JSON object of the tool's arguments.", ErrorCode.INVALID_INPUT)
    params = {key: value for key, value in raw_params.items() if value is not None}
    # The cursor is also one of the tool's own params: accept it there, and the explicit argument wins.
    inside = params.pop("cursor", None)
    cursor = start_cursor(args["cursor"] if args.get("cursor") is not None else inside)

    schema, fetch = _paged(name)
    # The same schema check Runner makes before a direct call, done here before any request leaves.
    inner = _validated(schema, params)
    if name == "search_substack" and not str(inner.get("query") or "").strip():
        raise ToolError("params.query must not be empty.", ErrorCode.INVALID_INPUT)
    # Stop starting pages a little before Runner's timeout, so a long run ends as a resumable answer
    # instead of a timeout that drops every item collected so far.
    deadline = time.monotonic() + TIMEOUT_S - RESERVE_S
    return await gather(name, lambda page_args: fetch(ctx, page_args), inner, limit, cursor, deadline)


def _paged(name: str) -> tuple[dict[str, Any], Any]:
    """The input schema of the tool and the function that answers one page.

    A @tool-decorated module attribute is its declaration, which holds the handler. A replacement
    function there, as a test fakes get_feed, answers the pages instead. Either way the collector runs
    the tool's own request, parsing and error mapping.
    """
    module = importlib.import_module(f"{__package__}.{name}")
    schema = getattr(module, "INPUT_SCHEMA", None)
    current = getattr(module, name, None)
    if not isinstance(schema, dict) or current is None:
        raise ToolError(f"No tool '{name}'.", ErrorCode.INVALID_INPUT)
    return schema, (current.handler if isinstance(current, ToolSpec) else current)


def _validated(schema: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """The tool's arguments without nulls, validated against its input schema as Runner does."""
    try:
        jsonschema.validate(params, schema)
    except jsonschema.ValidationError as exc:
        where = ".".join(str(part) for part in exc.absolute_path)
        raise ToolError(
            f"params.{where}: {exc.message}" if where else f"params: {exc.message}", ErrorCode.INVALID_INPUT
        ) from None
    return params
