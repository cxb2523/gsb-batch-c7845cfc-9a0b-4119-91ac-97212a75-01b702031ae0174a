# ruff: noqa: E402
import asyncio

import pytest

pytestmark = pytest.mark.kinesis

pytest.importorskip("kinesis", reason="Only works with async-kinesis installed")

from unittest.mock import AsyncMock, MagicMock

from kinesis.checkpointers import MemoryCheckPointer

from cratedb_toolkit.io.kinesis.gate import GatedCheckPointer


def test_gate_passes_through_checkpoints_when_healthy():
    checkpointer = MemoryCheckPointer(name="healthy")
    gate = GatedCheckPointer(checkpointer)

    async def scenario():
        assert (await gate.allocate("shard-0")) == (True, None)
        await gate.checkpoint("shard-0", "1")
        await gate.checkpoint("shard-0", "2")
        return checkpointer.get_all_checkpoints()

    checkpoints = asyncio.run(scenario())
    assert checkpoints["shard-0"]["sequence"] == "2"


def test_gate_freeze_blocks_later_checkpoint_but_keeps_last_safe_position():
    """The core guarantee: after a failed event the sequence never advances."""
    checkpointer = MemoryCheckPointer(name="frozen")
    gate = GatedCheckPointer(checkpointer)

    async def scenario():
        await gate.allocate("shard-0")
        # Events before the failure are processed and committed successfully.
        await gate.checkpoint("shard-0", "10")

        # A write fails: the relay freezes the shard.
        gate.freeze("shard-0")
        assert gate.is_frozen("shard-0")

        # Neither the failed event's sequence nor any later one may advance it.
        await gate.checkpoint("shard-0", "11")
        await gate.checkpoint("shard-0", "12")
        return checkpointer.get_all_checkpoints()

    checkpoints = asyncio.run(scenario())
    assert checkpoints["shard-0"]["sequence"] == "10"


def test_gate_freeze_without_shard_id_freezes_all_owned_shards():
    checkpointer = MemoryCheckPointer(name="multi")
    gate = GatedCheckPointer(checkpointer)

    async def scenario():
        await gate.allocate("shard-0")
        await gate.allocate("shard-1")
        await gate.checkpoint("shard-0", "1")
        await gate.checkpoint("shard-1", "1")

        # The handler does not know which shard a payload came from: freeze all.
        gate.freeze()
        await gate.checkpoint("shard-0", "2")
        await gate.checkpoint("shard-1", "2")
        return checkpointer.get_all_checkpoints()

    checkpoints = asyncio.run(scenario())
    assert checkpoints["shard-0"]["sequence"] == "1"
    assert checkpoints["shard-1"]["sequence"] == "1"
    assert gate.frozen_shards == {"shard-0", "shard-1"}


def test_gate_unrelated_shard_keeps_advancing():
    """Freezing one shard must not affect checkpointing of another shard."""
    checkpointer = MemoryCheckPointer(name="per-shard")
    gate = GatedCheckPointer(checkpointer)

    async def scenario():
        await gate.allocate("shard-0")
        await gate.allocate("shard-1")
        gate.freeze("shard-0")

        await gate.checkpoint("shard-0", "5")
        await gate.checkpoint("shard-1", "7")
        return checkpointer.get_all_checkpoints()

    checkpoints = asyncio.run(scenario())
    assert checkpoints["shard-0"]["sequence"] is None
    assert checkpoints["shard-1"]["sequence"] == "7"


def test_gate_deallocate_clears_freeze_state():
    checkpointer = MemoryCheckPointer(name="lifecycle")
    gate = GatedCheckPointer(checkpointer)

    async def scenario():
        await gate.allocate("shard-0")
        gate.freeze("shard-0")
        await gate.deallocate("shard-0")
        assert not gate.is_frozen("shard-0")
        assert "shard-0" not in gate.frozen_shards

    asyncio.run(scenario())


def test_gate_forwards_delegated_members():
    inner = MagicMock()
    inner.allocate = AsyncMock(return_value=(True, None))
    inner.checkpoint = AsyncMock()
    inner.close = AsyncMock()
    inner.get_all_checkpoints.return_value = {"shard-0": {"sequence": "3"}}
    inner.is_allocated.return_value = True
    inner.get_ref.return_value = "inner/1"
    gate = GatedCheckPointer(inner)
    collector = MagicMock()

    async def scenario():
        await gate.allocate("shard-0")
        await gate.checkpoint("shard-0", "3")
        assert gate.get_all_checkpoints() == {"shard-0": {"sequence": "3"}}
        assert gate.is_allocated("shard-0") is True
        assert gate.get_ref() == "inner/1"
        gate.bind_metrics(collector, {"stream_name": "demo"})
        await gate.close()

    asyncio.run(scenario())
    inner.allocate.assert_awaited_once_with("shard-0")
    inner.checkpoint.assert_awaited_once_with("shard-0", "3")
    inner.bind_metrics.assert_called_once_with(collector, {"stream_name": "demo"})
    inner.close.assert_awaited_once()


def test_gate_bind_metrics_optional():
    """Checkpointers without bind_metrics (exotic Protocol implementations) still work."""
    inner = MagicMock(spec=["allocate", "checkpoint", "deallocate", "close", "get_all_checkpoints"])
    gate = GatedCheckPointer(inner)
    # Must not raise even though the wrapped object lacks bind_metrics.
    gate.bind_metrics(MagicMock(), {"stream_name": "demo"})
