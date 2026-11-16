"""Per-channel settlement for get_daily_summary with fake API responses."""

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

from zulipchat_mcp.core.client import (
    DAILY_SUMMARY_MESSAGE_LIMIT,
    ZulipClientWrapper,
)


class FakeSdk:
    """Local stand-in for the Zulip SDK; no network or real realm involved."""

    def __init__(
        self,
        feature_level: int,
        responses: dict[str, Any],
        streams: dict[str, Any] | None = None,
    ) -> None:
        self.feature_level = feature_level
        self.responses = responses
        self.streams = streams or {"result": "success", "streams": []}
        self.requests: list[dict[str, Any]] = []
        self.stream_calls = 0

    def get_streams(self, **kwargs: Any) -> dict[str, Any]:
        self.stream_calls += 1
        return self.streams

    def get_messages(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        if request.get("anchor") == "date":
            return self.responses["date"]
        channel = request["narrow"][0]["operand"]
        return self.responses[channel]


def wrapper_for(sdk: FakeSdk) -> ZulipClientWrapper:
    config = MagicMock()
    config.get_zulip_client_config.return_value = {
        "email": "owner@example.com",
        "api_key": "fake",
        "site": "https://example.com",
    }
    wrapper = ZulipClientWrapper(config)
    wrapper._client = sdk
    return wrapper


def recent_message(channel: str, sender: str, topic: str = "t") -> dict[str, Any]:
    return {
        "display_recipient": channel,
        "sender_full_name": sender,
        "subject": topic,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }


def test_normal_multi_channel_settles_every_channel():
    sdk = FakeSdk(
        400,
        {
            "a": {
                "result": "success",
                "messages": [recent_message("a", "U1"), recent_message("a", "U1")],
            },
            "b": {
                "result": "success",
                "messages": [recent_message("b", "U2", "other")],
            },
        },
    )
    summary = wrapper_for(sdk).get_daily_summary(streams=["a", "b"])

    assert summary["total_messages"] == 3
    assert summary["streams"]["a"] == {"message_count": 2, "topics": {"t": 2}}
    assert summary["streams"]["b"] == {"message_count": 1, "topics": {"other": 1}}
    assert summary["top_senders"] == {"U1": 2, "U2": 1}
    assert summary["time_range"] == "Last 24 hours"
    assert "unavailable_streams" not in summary
    assert "truncated" not in summary
    settlement = summary["settlement"]
    assert settlement["status"] == "complete"
    assert settlement["mode"] == "per_channel"
    assert settlement["settled_streams"] == ["a", "b"]
    assert settlement["unsettled_streams"] == []
    assert settlement["failed_streams"] == []
    assert "interrupted" not in settlement


def test_one_failed_channel_does_not_stop_the_others():
    sdk = FakeSdk(
        400,
        {
            "a": {"result": "success", "messages": [recent_message("a", "U1")]},
            "broken": {
                "result": "error",
                "msg": "Invalid channel name",
                "code": "BAD_REQUEST",
            },
            "c": {"result": "success", "messages": [recent_message("c", "U3")]},
        },
    )
    summary = wrapper_for(sdk).get_daily_summary(streams=["a", "broken", "c"])

    # Successful channels still count; old fields keep their shape.
    assert summary["total_messages"] == 2
    assert summary["streams"]["a"]["message_count"] == 1
    assert summary["streams"]["c"]["message_count"] == 1
    assert "broken" not in summary["streams"]
    # Legacy channel-name list survives, now with a reason beside it.
    assert summary["unavailable_streams"] == ["broken"]
    assert summary["unavailable_stream_reasons"]["broken"] == "Invalid channel name"

    settlement = summary["settlement"]
    assert settlement["status"] == "partial"
    assert settlement["settled_streams"] == ["a", "c"]
    assert settlement["unsettled_streams"] == ["broken"]
    assert settlement["failed_streams"] == [
        {"stream": "broken", "reason": "Invalid channel name", "code": "BAD_REQUEST"}
    ]
    assert "interrupted" not in settlement


def test_channel_exception_is_settled_without_aborting():
    class ExplodingSdk(FakeSdk):
        def get_messages(self, request: dict[str, Any]) -> dict[str, Any]:
            channel = request["narrow"][0]["operand"]
            if channel == "boom":
                raise RuntimeError("connection reset")
            return super().get_messages(request)

    sdk = ExplodingSdk(
        400,
        {
            "a": {"result": "success", "messages": [recent_message("a", "U1")]},
            "c": {"result": "success", "messages": [recent_message("c", "U3")]},
        },
    )
    summary = wrapper_for(sdk).get_daily_summary(streams=["boom", "a", "c"])

    assert summary["total_messages"] == 2
    assert summary["unavailable_streams"] == ["boom"]
    assert summary["unavailable_stream_reasons"]["boom"] == "connection reset"
    assert summary["settlement"]["settled_streams"] == ["a", "c"]


def test_rate_limit_stops_new_requests_and_keeps_collected_counts():
    sdk = FakeSdk(
        400,
        {
            "a": {"result": "success", "messages": [recent_message("a", "U1")]},
            "b": {
                "result": "error",
                "code": "RATE_LIMIT_HIT",
                "msg": "Zulip API usage exceeded rate limit",
                "retry-after": 7,
            },
            "c": {"result": "success", "messages": [recent_message("c", "U3")]},
            "d": {"result": "success", "messages": [recent_message("d", "U4")]},
        },
    )
    summary = wrapper_for(sdk).get_daily_summary(streams=["a", "b", "c", "d"])

    # No exception and no requests after the throttled one.
    assert [request["narrow"][0]["operand"] for request in sdk.requests] == ["a", "b"]
    assert summary["total_messages"] == 1
    assert summary["streams"]["a"]["message_count"] == 1
    assert "c" not in summary["streams"] and "d" not in summary["streams"]
    # The throttled channel is still a failure with its reason available.
    assert summary["unavailable_streams"] == ["b"]
    assert "rate limit" in summary["unavailable_stream_reasons"]["b"]

    settlement = summary["settlement"]
    assert settlement["status"] == "partial"
    assert settlement["settled_streams"] == ["a"]
    assert settlement["unsettled_streams"] == ["b", "c", "d"]
    assert settlement["interrupted"]["kind"] == "rate_limited"
    assert settlement["interrupted"]["stream"] == "b"
    assert settlement["interrupted"]["code"] == "RATE_LIMIT_HIT"
    assert settlement["interrupted"]["retry_after_seconds"] == 7


def test_retry_after_on_success_keeps_counts_and_stops_next_requests():
    sdk = FakeSdk(
        400,
        {
            "a": {
                "result": "success",
                "messages": [recent_message("a", "U1")],
                "retry-after": 3,
            },
            "b": {"result": "success", "messages": [recent_message("b", "U2")]},
        },
    )
    summary = wrapper_for(sdk).get_daily_summary(streams=["a", "b"])

    # The successful answer is counted; the throttle only stops the next call.
    assert [request["narrow"][0]["operand"] for request in sdk.requests] == ["a"]
    assert summary["total_messages"] == 1
    assert summary["streams"]["a"]["message_count"] == 1
    settlement = summary["settlement"]
    assert settlement["interrupted"]["retry_after_seconds"] == 3
    assert settlement["settled_streams"] == ["a"]
    assert settlement["unsettled_streams"] == ["b"]


def test_rate_limit_while_listing_channels_is_reported_not_raised():
    sdk = FakeSdk(
        400,
        {},
        streams={
            "result": "error",
            "code": "RATE_LIMIT_HIT",
            "msg": "Zulip API usage exceeded rate limit",
            "retry-after": 2,
        },
    )
    summary = wrapper_for(sdk).get_daily_summary()

    assert sdk.requests == []
    assert summary["total_messages"] == 0
    assert summary["streams"] == {}
    settlement = summary["settlement"]
    assert settlement["status"] == "partial"
    assert settlement["interrupted"]["kind"] == "rate_limited"
    assert settlement["interrupted"]["retry_after_seconds"] == 2
    assert settlement["settled_streams"] == []
    assert settlement["unsettled_scope"] == "channel_listing"


def test_date_anchored_request_names_the_truncated_channels():
    now = datetime.now(timezone.utc).timestamp()
    sdk = FakeSdk(
        513,
        {
            "date": {
                "result": "success",
                "found_newest": False,
                "messages": [
                    {
                        "display_recipient": "a",
                        "sender_full_name": "U1",
                        "subject": "t",
                        "timestamp": now,
                    },
                    {
                        "display_recipient": "b",
                        "sender_full_name": "U2",
                        "subject": "t",
                        "timestamp": now,
                    },
                    {
                        "display_recipient": "a",
                        "sender_full_name": "U1",
                        "subject": "old",
                        "timestamp": now - 3 * 86400,
                    },
                ],
            }
        },
    )
    summary = wrapper_for(sdk).get_daily_summary(hours_back=24)

    assert len(sdk.requests) == 1
    assert summary["total_messages"] == 2
    assert summary["truncated"] is True
    assert summary["truncated_streams"] == ["a", "b"]
    settlement = summary["settlement"]
    assert settlement["mode"] == "date_anchored"
    assert settlement["status"] == "partial"
    assert settlement["settled_streams"] == ["a", "b"]
    assert settlement["truncation"]["message_limit"] == DAILY_SUMMARY_MESSAGE_LIMIT
    assert settlement["truncation"]["affected_streams"] == ["a", "b"]


def test_date_anchored_limit_reached_sets_truncated():
    now = datetime.now(timezone.utc).timestamp()
    messages = [
        {
            "display_recipient": f"c{index % 3}",
            "sender_full_name": "U",
            "subject": "t",
            "timestamp": now,
        }
        for index in range(DAILY_SUMMARY_MESSAGE_LIMIT)
    ]
    sdk = FakeSdk(
        513,
        {"date": {"result": "success", "found_newest": True, "messages": messages}},
    )
    summary = wrapper_for(sdk).get_daily_summary(hours_back=24)

    assert summary["total_messages"] == DAILY_SUMMARY_MESSAGE_LIMIT
    assert summary["truncated"] is True
    assert summary["truncated_streams"] == ["c0", "c1", "c2"]
    assert summary["settlement"]["truncation"]["returned_messages"] == (
        DAILY_SUMMARY_MESSAGE_LIMIT
    )


def test_date_anchored_complete_window_has_no_truncation_fields():
    now = datetime.now(timezone.utc).timestamp()
    sdk = FakeSdk(
        513,
        {
            "date": {
                "result": "success",
                "found_newest": True,
                "messages": [
                    {
                        "display_recipient": "a",
                        "sender_full_name": "U1",
                        "subject": "t",
                        "timestamp": now,
                    }
                ],
            }
        },
    )
    summary = wrapper_for(sdk).get_daily_summary(hours_back=24)

    assert "truncated" not in summary
    assert "truncated_streams" not in summary
    settlement = summary["settlement"]
    assert settlement["status"] == "complete"
    assert settlement["settled_streams"] == ["a"]
    assert "truncation" not in settlement


def test_date_anchored_rate_limit_returns_empty_counts():
    sdk = FakeSdk(
        513,
        {
            "date": {
                "result": "error",
                "code": "RATE_LIMIT_HIT",
                "msg": "Zulip API usage exceeded rate limit",
                "retry-after": 4,
            }
        },
    )
    summary = wrapper_for(sdk).get_daily_summary(hours_back=24)

    assert summary["total_messages"] == 0
    assert summary["streams"] == {}
    assert summary["top_senders"] == {}
    assert summary["time_range"] == "Last 24 hours"
    settlement = summary["settlement"]
    assert settlement["status"] == "partial"
    assert settlement["interrupted"]["retry_after_seconds"] == 4
    assert settlement["settled_streams"] == []
    assert settlement["unsettled_scope"] == "all_public_channels"


def test_tool_surface_keeps_status_success_and_exposes_settlement(monkeypatch):
    from zulipchat_mcp.tools import search

    sdk = FakeSdk(
        400,
        {
            "a": {"result": "success", "messages": [recent_message("a", "U1")]},
            "b": {"result": "error", "msg": "Not subscribed", "code": "BAD_REQUEST"},
        },
    )
    wrapper = wrapper_for(sdk)
    monkeypatch.setattr(search, "get_client", lambda: wrapper)

    output = asyncio.run(search.get_daily_summary(streams=["a", "b"]))

    assert output["status"] == "success"
    assert output["hours_back"] == 24
    assert output["summary"]["total_messages"] == 1
    assert output["summary"]["unavailable_streams"] == ["b"]
    assert output["summary"]["settlement"]["status"] == "partial"


def test_summary_keeps_legacy_key_names_and_types():
    sdk = FakeSdk(400, {"a": {"result": "success", "messages": []}})
    summary = wrapper_for(sdk).get_daily_summary(streams=["a"])

    for key, kind in (
        ("total_messages", int),
        ("streams", dict),
        ("top_senders", dict),
        ("time_range", str),
        ("settlement", dict),
    ):
        assert isinstance(summary[key], kind)
