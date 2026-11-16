from typing import (
    Any,
)
from unittest.mock import (
    Mock,
)

import pytest
from pytest_mock import (
    MockerFixture,
)

from adapters.config import (
    fetch_and_write_configs,
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
)
from domain.config import (
    ConfigExtractionResult,
)


def _make_runtime_ctx() -> RuntimeContext:
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
                batch_size=10,
            ),
            config_extraction=ConfigExtractionContext(
                batch_size=1,
                max_concurrent_channels=1,
            ),
        ),
    )


def _make_channels() -> ChannelsDict:
    return {
        "alpha": {
            "count": 0,
            "current_id": 1,
            "last_id": 3,
            "state": 1,
        },
        "beta": {
            "count": 0,
            "current_id": 1,
            "last_id": 3,
            "state": 1,
        },
        "gamma": {
            "count": 0,
            "current_id": 1,
            "last_id": 3,
            "state": 1,
        },
    }


@pytest.fixture
def mock_channel_processing(
    mocker: MockerFixture,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "failing": set(),
        "processed": [],
    }

    async def fake_process_channel_configs(
        ctx: HttpContext,
        *,
        channel_name: ChannelName,
        channel_info: ChannelInfo,
        progress: object,
        overall_task: object,
        batch_size: int,
        configs_path: object,
    ) -> ConfigExtractionResult:
        if channel_name in state["failing"]:
            channel_info["current_id"] = 999
            raise RuntimeError(channel_name)

        state["processed"].append(channel_name)

        return ConfigExtractionResult(
            channel_name=channel_name,
            total_found=0,
            new_found=1,
        )

    mocker.patch(
        "adapters.config._process_channel_configs",
        side_effect=fake_process_channel_configs,
    )

    return state


async def test_fetch_continues_after_single_channel_failure(
    mock_channel_processing: dict[str, Any],
    mocker: MockerFixture,
) -> None:
    mock_channel_processing["failing"].add("beta")

    channels = _make_channels()
    mocker.patch(
        "adapters.config.render_config_extract",
    )

    summary = await fetch_and_write_configs(
        ctx=_make_runtime_ctx(),
        channels=channels,
    )

    assert mock_channel_processing["processed"] == [
        "alpha",
        "gamma",
    ]
    assert summary.channels_count == 3
    assert summary.failed_count == 1
    assert [
        failure.channel_name
        for failure in summary.failures
    ] == ["beta"]
    assert summary.failures[0].exc_type == "RuntimeError"
    assert summary.configs_count == 2

    assert channels["beta"]["current_id"] == 1


async def test_fetch_all_success_matches_previous_summary(
    mock_channel_processing: dict[str, Any],
) -> None:
    channels = _make_channels()

    summary = await fetch_and_write_configs(
        ctx=_make_runtime_ctx(),
        channels=channels,
    )

    assert summary.failed_count == 0
    assert summary.channels_count == 3
    assert summary.configs_count == 3
    assert {
        result.channel_name
        for result in summary.results
    } == {
        "alpha",
        "beta",
        "gamma",
    }


async def test_fetch_without_channels_returns_empty_summary(
) -> None:
    summary = await fetch_and_write_configs(
        ctx=_make_runtime_ctx(),
        channels={},
    )

    assert summary.channels_count == 0
    assert summary.configs_count == 0
    assert summary.failures == ()
