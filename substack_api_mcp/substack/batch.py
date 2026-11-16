"""Collect page after page of a reader list, keeping the pages that worked.

`get_feed` and `search_substack` answer one page and hand back Substack's `next_cursor`. A script that
follows the cursor loses every item it already had when Substack rate limits it or signs the session out
on a later page. `collect_pages` fetches page by page, appends the items of each page that worked, stops
at the first failed page and says how far it got, why it stopped and where to resume.
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from substack_api_mcp.core.errors import ErrorCode, ToolError

PAGED_TOOLS = ("get_feed", "search_substack")
# The list fields of each tool's answer that are appended across pages.
_ITEM_KEYS = {"get_feed": ("posts", "notes"), "search_substack": ("posts", "notes", "people")}
# How many pages one call may ask for, so a forgotten cap cannot walk the feed for hours.
MAX_PAGES = 100
# A starting cursor must be a next_cursor an earlier call handed out: Substack's base64 nextCursor (of
# get_feed and search_substack), or a decimal page cursor of our offset lists. Anything else, such as
# "invalid" or "not-a-cursor", is refused before any request leaves.
_MAX_CURSOR = 4096
_BASE64 = re.compile(r"^[A-Za-z0-9+/_=-]+$")
_DIGITS = re.compile(r"^\d+$")

PageFetcher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _base64_text(value: str) -> bool:
    """The text decodes as base64 into a run of printable ASCII, as Substack's nextCursor does."""
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        text = raw.decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    return len(text) >= 4 and text.isprintable()


def _looks_like_cursor(value: str) -> bool:
    """Base64 as Substack sends in next_cursor, or a page number of our offset lists."""
    if not value or len(value) > _MAX_CURSOR or not value.isascii():
        return False
    if _DIGITS.match(value):
        return int(value) >= 1
    return bool(_BASE64.match(value)) and _base64_text(value)


def start_cursor(value: Any) -> str | None:
    """The optional next_cursor from an earlier page: none, or text shaped like one of Substack's cursors."""
    if value is None:
        return None
    if not isinstance(value, str) or not _looks_like_cursor(value):
        raise ToolError("cursor must be the next_cursor from an earlier page.", ErrorCode.INVALID_INPUT)
    return value


def page_limit(value: Any) -> int:
    """`max_pages` as a whole number from 1 to MAX_PAGES (3.0 counts as 3, "3" does not)."""
    whole = isinstance(value, int) and not isinstance(value, bool)
    whole = whole or (isinstance(value, float) and value.is_integer())
    if not whole or not 1 <= int(value) <= MAX_PAGES:
        raise ToolError(f"max_pages must be a whole number from 1 to {MAX_PAGES}.", ErrorCode.INVALID_INPUT)
    return int(value)


async def collect_pages(
    tool: str,
    fetch_page: PageFetcher,
    params: dict[str, Any],
    max_pages: int,
    cursor: str | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Fetch up to max_pages pages, keep every item, and report the exact state of the collection.

    A page that raises ToolError stops the run: its code and message become `stop_reason` and
    `stop_message`, the items of earlier pages stay, and `next_cursor` is the cursor of the failed page
    so the caller can resume there. Any other exception is reported the same way as `internal_error`.
    Reaching max_pages while Substack still offers a cursor stops with `page_limit`; so does running
    out of time before the next page starts (`time_limit`, `deadline` is time.monotonic()). `complete`
    is true only when Substack had no next page, so a caller never mistakes a capped or interrupted
    run for the whole list.
    """
    keys = _ITEM_KEYS[tool]
    items: dict[str, list[dict[str, Any]]] = {key: [] for key in keys}
    pages: list[dict[str, Any]] = []
    fetched = 0
    stop_reason: str | None = None
    stop_message: str | None = None

    while fetched < max_pages:
        if deadline is not None and time.monotonic() >= deadline:
            # The call is about to hit the runner's timeout, which would throw away every page so far.
            stop_reason = "time_limit"
            stop_message = "Stopped before this call ran out of time; pass next_cursor as cursor to continue."
            pages.append({"page": fetched + 1, "cursor": cursor, "error": stop_reason, "message": stop_message})
            break
        args = dict(params)
        if cursor:
            args["cursor"] = cursor
        try:
            page = await fetch_page(args)
        except ToolError as exc:
            stop_reason, stop_message = exc.code.value, str(exc)
        except Exception as exc:  # noqa: BLE001 - a broken page must not throw away the pages before it
            stop_reason, stop_message = ErrorCode.INTERNAL_ERROR.value, f"{type(exc).__name__}: {exc}"
        else:
            fetched += 1
            record: dict[str, Any] = {"page": fetched, "cursor": cursor}
            for key in keys:
                got = page.get(key) or []
                items[key].extend(got)
                record[key] = len(got)
            pages.append(record)
            following = page.get("next_cursor")
            if not following:
                cursor = None
                break
            cursor = str(following)
            continue
        # This page failed: name it, keep the pages before it, and leave its cursor for a resume.
        pages.append({"page": fetched + 1, "cursor": cursor, "error": stop_reason, "message": stop_message})
        break
    else:
        # max_pages pages worked and Substack still offers more: a cap, not an error.
        stop_reason = "page_limit"
        stop_message = f"Stopped after {max_pages} pages; pass next_cursor as cursor to continue."

    return {
        "tool": tool,
        "params": dict(params),
        **items,
        "pages_fetched": fetched,
        "complete": stop_reason is None,
        "stop_reason": stop_reason,
        "stop_message": stop_message,
        "next_cursor": cursor,
        "pages": pages,
    }
