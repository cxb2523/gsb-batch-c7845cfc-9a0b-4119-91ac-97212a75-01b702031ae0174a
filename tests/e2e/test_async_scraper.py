from json import (
    dumps,
    loads,
)
from pathlib import (
    Path,
)
from types import (
    SimpleNamespace,
)
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

from adapters.channel import (
    load_channels,
    save_channels,
)
from adapters.scraper import (
    update_channels_info,
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

_CHANNEL_BLANK: ChannelInfo = {
    "count": 0,
    "current_id": 1,
    "last_id": -1,
    "state": 0,
}


def _make_io_ctx(
    tmp_path: Path,
) -> IOContext:
    return IOContext(
        channels_path=tmp_path / "current.json",
        configs_raw_path=tmp_path / "v2ray-raw.txt",
    )


def _make_runtime_ctx(
    *,
    io_ctx: IOContext,
    batch_size: int = 10,
) -> RuntimeContext:
    return RuntimeContext(
        http=HttpContext(
            client=Mock(),
            retries=1,
            retry_delay=0,
        ),
        io=io_ctx,
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


def _write_channels(
    path: Path,
    channels: ChannelsDict,
) -> None:
    path.write_text(
        dumps(
            obj=channels,
            ensure_ascii=False,
            indent=4,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _read_channels(
    path: Path,
) -> ChannelsDict:
    return loads(
        s=path.read_text(encoding="utf-8"),
    )


@pytest.fixture
def io_ctx(
    tmp_path: Path,
) -> IOContext:
    return _make_io_ctx(
        tmp_path=tmp_path,
    )


@pytest.fixture
def fake_post_ids(
    mocker: MockerFixture,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "last_ids": {
            "alpha": 30,
            "beta": 20,
            "gamma": 10,
        },
        "failing": set(),
    }

    async def fake_get_last_post_id(
        ctx: HttpContext,
        *,
        channel_name: ChannelName,
    ) -> PostID:
        if channel_name in state["failing"]:
            raise ConnectionError(channel_name)

        return state["last_ids"][channel_name]  # type: ignore[no-any-return]

    async def fake_get_first_post_id(
        ctx: HttpContext,
        *,
        channel_name: ChannelName,
    ) -> PostID:
        return 1

    mocker.patch(
        "adapters.scraper.get_last_post_id",
        side_effect=fake_get_last_post_id,
    )
    mocker.patch(
        "adapters.scraper.get_first_post_id",
        side_effect=fake_get_first_post_id,
    )

    return state


async def test_all_success_updates_every_channel_on_disk(
    io_ctx: IOContext,
    fake_post_ids: dict[str, Any],
    mock_live: Mock,
) -> None:
    initial: ChannelsDict = {
        name: dict(_CHANNEL_BLANK)
        for name in (
            "alpha",
            "beta",
            "gamma",
        )
    }
    _write_channels(
        io_ctx.channels_path,
        initial,
    )

    channels = await load_channels(
        ctx=io_ctx,
    )
    runtime_ctx = _make_runtime_ctx(
        io_ctx=io_ctx,
    )

    summary = await update_channels_info(
        ctx=runtime_ctx,
        channels=channels,
    )
    await save_channels(
        ctx=io_ctx,
        channels=channels,
    )

    assert summary.failed_count == 0
    assert summary.changed_count == 3

    saved = _read_channels(io_ctx.channels_path)

    for name, last_id in fake_post_ids["last_ids"].items():
        assert saved[name]["last_id"] == last_id
        assert saved[name]["state"] == 1
        assert saved[name]["current_id"] == 1


async def test_single_refresh_failure_does_not_stop_other_channels(
    io_ctx: IOContext,
    fake_post_ids: dict[str, Any],
    mock_live: Mock,
) -> None:
    fake_post_ids["failing"].add("beta")

    failed_channel: ChannelInfo = {
        "count": 7,
        "current_id": 3,
        "last_id": 11,
        "state": 1,
    }
    initial: ChannelsDict = {
        "alpha": dict(_CHANNEL_BLANK),
        "beta": dict(failed_channel),
        "gamma": dict(_CHANNEL_BLANK),
    }
    _write_channels(
        io_ctx.channels_path,
        initial,
    )

    channels = await load_channels(
        ctx=io_ctx,
    )
    runtime_ctx = _make_runtime_ctx(
        io_ctx=io_ctx,
        batch_size=1,
    )

    summary = await update_channels_info(
        ctx=runtime_ctx,
        channels=channels,
    )
    await save_channels(
        ctx=io_ctx,
        channels=channels,
    )

    assert summary.failed_count == 1
    assert [
        failure.channel_name
        for failure in summary.failures
    ] == ["beta"]
    assert summary.failures[0].exc_type == "ConnectionError"

    saved = _read_channels(io_ctx.channels_path)

    assert saved["alpha"]["last_id"] == 30
    assert saved["alpha"]["state"] == 1
    assert saved["gamma"]["last_id"] == 10
    assert saved["gamma"]["state"] == 1

    assert saved["beta"] == failed_channel


async def test_failed_channel_is_not_marked_available(
    io_ctx: IOContext,
    fake_post_ids: dict[str, Any],
    mock_live: Mock,
) -> None:
    fake_post_ids["failing"].add("alpha")

    initial: ChannelsDict = {
        "alpha": dict(_CHANNEL_BLANK),
    }
    _write_channels(
        io_ctx.channels_path,
        initial,
    )

    channels = await load_channels(
        ctx=io_ctx,
    )

    summary = await update_channels_info(
        ctx=_make_runtime_ctx(
            io_ctx=io_ctx,
        ),
        channels=channels,
    )

    assert summary.failed_count == 1
    assert channels["alpha"]["last_id"] == -1
    assert channels["alpha"]["state"] == 0


def _make_script_args(
    *,
    channels_path: Path,
    configs_raw_path: Path,
) -> SimpleNamespace:
    return SimpleNamespace(
        channels_path=str(channels_path),
        configs_raw_path=str(configs_raw_path),
        proxy_url=None,
        retries=1,
        retry_delay=0,
        time_out=1.0,
        channels_batch=10,
        configs_batch=10,
        channels_concurrency=2,
        skip_update=False,
    )


def _summary(
    channel_names: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        failures=tuple(
            SimpleNamespace(channel_name=name)
            for name in channel_names
        ),
        failed_count=len(channel_names),
    )


async def _run_script_main(
    *,
    tmp_path: Path,
    mocker: MockerFixture,
    refresh_summary: SimpleNamespace,
    fetch_summary: SimpleNamespace | None = None,
    fetch_error: Exception | None = None,
) -> Mock:
    from scripts import (  # noqa: PLC0415
        scraper as scraper_script,
    )

    mocker.patch.object(
        scraper_script,
        "parse_args",
        return_value=_make_script_args(
            channels_path=tmp_path / "current.json",
            configs_raw_path=tmp_path / "raw.txt",
        ),
    )
    mocker.patch.object(
        scraper_script,
        "display_channel_info",
    )
    mocker.patch.object(
        scraper_script,
        "update_channels_info",
        new=mocker.AsyncMock(
            return_value=refresh_summary,
        ),
    )
    mocker.patch.object(
        scraper_script,
        "fetch_and_write_configs",
        new=mocker.AsyncMock(
            return_value=fetch_summary,
            side_effect=fetch_error,
        ),
    )
    nested_logger = mocker.patch.object(
        scraper_script,
        "logger",
    )

    await scraper_script.main()

    return nested_logger


def _error_messages(
    nested_logger: Mock,
) -> list[str]:
    return [
        call.kwargs["msg"]
        for call in nested_logger.error.call_args_list
    ]


async def test_scripts_main_saves_channels_when_fetch_raises(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    channels_path = tmp_path / "current.json"
    initial: ChannelsDict = {
        "alpha": {
            "count": 0,
            "current_id": 4,
            "last_id": 9,
            "state": 1,
        },
    }
    _write_channels(
        channels_path,
        initial,
    )

    nested_logger = await _run_script_main(
        tmp_path=tmp_path,
        mocker=mocker,
        refresh_summary=_summary(),
        fetch_error=RuntimeError("boom"),
    )

    saved = _read_channels(channels_path)

    assert saved["alpha"]["current_id"] == 4
    assert saved["alpha"]["last_id"] == 9

    assert any(
        "failed channel" in message
        for message in _error_messages(nested_logger)
    )


async def test_scripts_main_reports_both_failure_reasons(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    _write_channels(
        tmp_path / "current.json",
        {
            "alpha": dict(_CHANNEL_BLANK),
        },
    )

    nested_logger = await _run_script_main(
        tmp_path=tmp_path,
        mocker=mocker,
        refresh_summary=_summary(("alpha",)),
        fetch_summary=_summary(("beta", "gamma")),
    )

    summary_messages = [
        message
        for message in _error_messages(nested_logger)
        if "failed" in message
    ]

    assert len(summary_messages) == 1

    message = summary_messages[0]

    assert "3 failed channel(s)" in message
    assert "1 failed during channel refresh" in message
    assert "2 failed during config extraction" in message


async def test_failures_summary_is_silent_when_nothing_failed(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    _write_channels(
        tmp_path / "current.json",
        {
            "alpha": dict(_CHANNEL_BLANK),
        },
    )

    nested_logger = await _run_script_main(
        tmp_path=tmp_path,
        mocker=mocker,
        refresh_summary=_summary(),
        fetch_summary=_summary(),
    )

    assert not any(
        "failed channel" in message
        for message in _error_messages(nested_logger)
    )


async def test_aborted_fetch_summary_does_not_claim_channel_count(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    _write_channels(
        tmp_path / "current.json",
        {
            "alpha": dict(_CHANNEL_BLANK),
        },
    )

    nested_logger = await _run_script_main(
        tmp_path=tmp_path,
        mocker=mocker,
        refresh_summary=_summary(),
        fetch_error=RuntimeError("boom"),
    )

    summary_messages = [
        message
        for message in _error_messages(nested_logger)
        if "Scraping finished" in message
    ]

    assert len(summary_messages) == 1
    assert "remaining channels were not processed" in summary_messages[0]
