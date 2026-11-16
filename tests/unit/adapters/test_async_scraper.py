from typing import (
    Any,
)
from unittest.mock import (
    Mock,
)

import pytest
from httpx import (
    ConnectError,
)
from pytest_mock import (
    MockerFixture,
)

from adapters.scraper import (
    update_channels_info,
)
from core.constants.common import (
    DEFAULT_CHANNEL_VALUES,
)
from core.context import (
    ChannelUpdateContext,
    ConfigExtractionContext,
    HttpContext,
    IOContext,
    PipelineRuntimeContext,
    RuntimeContext,
)
from core.typing import (
    ChannelInfo,
    ChannelName,
    ChannelsDict,
    PostID,
)
from domain.channel import (
    ChannelUpdateResult,
    ChannelUpdateSummary,
    update_last_id_and_state,
)

_CONNECT_ERROR_MESSAGE = "connection refused"
_RUNTIME_ERROR_MESSAGE = "unexpected failure"


def _make_runtime_ctx(
    *,
    batch_size: int = 10,
) -> RuntimeContext:
    return RuntimeContext(
        http=HttpContext(
            client=Mock(),
            retries=1,
            retry_delay=0,
        ),
        io=IOContext(
            channels_path="unused.json",
            configs_raw_path="unused.txt",
        ),
        pipeline=PipelineRuntimeContext(
            channel_update=ChannelUpdateContext(
                batch_size=batch_size,
            ),
            config_extraction=ConfigExtractionContext(
                batch_size=1,
                max_concurrent_channels=1,
            ),
        ),
    )


def _make_channels(
    names: list[ChannelName],
    *,
    overrides: dict[ChannelName, ChannelInfo] | None = None,
) -> ChannelsDict:
    channels: ChannelsDict = {
        name: DEFAULT_CHANNEL_VALUES.copy()
        for name in names
    }
    channels.update(overrides or {})

    return channels


@pytest.fixture
def mock_post_id_fetchers(
    mocker: MockerFixture,
) -> dict[str, Any]:
    last_ids = {
        "alpha": 300,
        "beta": 200,
        "gamma": 100,
        "delta": 50,
    }
    failing: set[ChannelName] = set()

    async def fake_get_last_post_id(
        ctx: HttpContext,
        *,
        channel_name: ChannelName,
    ) -> PostID:
        if channel_name in failing:
            raise ConnectError(_CONNECT_ERROR_MESSAGE)

        return last_ids[channel_name]

    async def fake_get_first_post_id(
        ctx: HttpContext,
        *,
        channel_name: ChannelName,
    ) -> PostID:
        return 7

    mocker.patch(
        "adapters.scraper.get_last_post_id",
        side_effect=fake_get_last_post_id,
    )
    mocker.patch(
        "adapters.scraper.get_first_post_id",
        side_effect=fake_get_first_post_id,
    )

    return {
        "last_ids": last_ids,
        "failing": failing,
    }


async def test_update_channels_info_all_success(
    mocker: MockerFixture,
    mock_live: Mock,
    mock_post_id_fetchers: dict[str, Any],
) -> None:
    channels = _make_channels([
        "alpha",
        "beta",
        "gamma",
    ])

    summary = await update_channels_info(
        ctx=_make_runtime_ctx(),
        channels=channels,
    )

    assert isinstance(
        summary,
        ChannelUpdateSummary,
    )
    assert summary.failures == ()
    assert summary.failed_count == 0
    assert summary.changed_count == 3

    for name, last_id in mock_post_id_fetchers["last_ids"].items():
        if name not in channels:
            continue

        assert channels[name]["last_id"] == last_id
        assert channels[name]["state"] == 1
        assert channels[name]["current_id"] == 7


async def test_update_channels_info_isolates_refresh_failure(
    mocker: MockerFixture,
    mock_live: Mock,
    mock_post_id_fetchers: dict[str, Any],
) -> None:
    mock_post_id_fetchers["failing"].add("beta")

    failing_channel: ChannelInfo = {
        "count": 5,
        "current_id": 10,
        "last_id": 20,
        "state": 1,
    }
    channels = _make_channels(
        [
            "alpha",
            "beta",
            "gamma",
            "delta",
        ],
        overrides={
            "beta": failing_channel.copy(),
        },
    )

    summary = await update_channels_info(
        ctx=_make_runtime_ctx(batch_size=1),
        channels=channels,
    )

    assert summary.changed_count == 3
    assert summary.failed_count == 1
    assert [
        failure.channel_name
        for failure in summary.failures
    ] == ["beta"]
    assert summary.failures[0].exc_type == "ConnectError"

    for name in ("alpha", "gamma", "delta"):
        assert channels[name]["last_id"] == (
            mock_post_id_fetchers["last_ids"][name]
        )
        assert channels[name]["state"] == 1

    assert channels["beta"] == failing_channel


async def test_update_channels_info_restores_mutated_failed_channel(
    mocker: MockerFixture,
    mock_live: Mock,
) -> None:
    original: ChannelInfo = {
        "count": 5,
        "current_id": 10,
        "last_id": 20,
        "state": 1,
    }
    channels = _make_channels(
        [
            "alpha",
            "beta",
        ],
        overrides={
            "beta": original.copy(),
        },
    )

    async def fake_update_channel_info(
        ctx: HttpContext,
        *,
        channel_name: ChannelName,
        channel_info: ChannelInfo,
    ) -> ChannelUpdateResult:
        if channel_name == "beta":
            channel_info["last_id"] = 999
            channel_info["state"] = 1
            raise RuntimeError(_RUNTIME_ERROR_MESSAGE)

        return update_last_id_and_state(
            channel_name=channel_name,
            channel_info=channel_info,
            last_post_id=123,
        )

    mocker.patch(
        "adapters.scraper._update_channel_info",
        side_effect=fake_update_channel_info,
    )

    summary = await update_channels_info(
        ctx=_make_runtime_ctx(),
        channels=channels,
    )

    assert channels["beta"] == original
    assert summary.failed_count == 1
    assert channels["alpha"]["last_id"] == 123


async def test_update_channels_info_skip_update(
    mock_live: Mock,
) -> None:
    channels = _make_channels([
        "alpha",
    ])

    summary = await update_channels_info(
        ctx=_make_runtime_ctx(),
        channels=channels,
        skip_update=True,
    )

    assert summary.changed_count == 0
    assert summary.failed_count == 0
    assert channels["alpha"] == DEFAULT_CHANNEL_VALUES
