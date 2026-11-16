# ruff: noqa: E402
import asyncio
from unittest.mock import MagicMock

import pytest
from commons_codec.model import SQLOperation
from crate.client.exceptions import ConnectionError as CrateConnectionError
from kinesis import Consumer
from kinesis.checkpointers import MemoryCheckPointer
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.kinesis

pytest.importorskip("kinesis", reason="Only works with async-kinesis installed")

from cratedb_toolkit.io.kinesis.adapter import KinesisStreamAdapter
from cratedb_toolkit.io.kinesis.gate import GatedCheckPointer
from cratedb_toolkit.io.kinesis.relay import KinesisRelay

SHARD = "shard-0"


@pytest.fixture
def relay_factory(mocker):
    mocker.patch("cratedb_toolkit.io.kinesis.relay.KinesisAdapterBase.factory")
    mocker.patch("cratedb_toolkit.io.kinesis.relay.DatabaseAdapter")

    def factory(**kwargs):
        relay = KinesisRelay("kinesis+dms://localhost/demo", "crate://localhost/testdrive/demo", **kwargs)
        relay.connection = MagicMock()
        relay.progress_bar = MagicMock()
        relay.translator = MagicMock()
        relay.translator.to_sql.return_value = SQLOperation(statement="SELECT :id", parameters={"id": 1})
        return relay

    return factory


def _checkpoint(sequence):
    return {"__CHECKPOINT__": {"ShardId": SHARD, "SequenceNumber": str(sequence)}}


async def _drive_consumer(relay, queue_items, expect_error=None):
    """
    Feed a real ``Consumer`` queue directly, exercising the production
    sentinel -> deferred-checkpoint -> checkpointer path without a fetcher.

    The relay and the consumer share the same gate, mirroring how
    ``_setup_checkpointer()`` wires a single wrapped checkpointer into both.
    """
    assert relay.checkpoint_gate is not None
    gate = relay.checkpoint_gate
    consumer = Consumer(stream_name="demo", checkpointer=gate, idle_timeout=0.01)  # ty: ignore[invalid-argument-type]
    consumer.shards = [{"ShardId": SHARD}]
    await gate.allocate(SHARD)
    consumer.fetch_task = asyncio.create_task(asyncio.sleep(60))
    for item in queue_items:
        await consumer.queue.put(item)
    try:
        if expect_error is not None:
            with pytest.raises(expect_error):
                async for event in consumer:
                    relay.process_event(event)
        else:
            async for event in consumer:
                relay.process_event(event)
    finally:
        fetch_task = consumer.fetch_task
        await consumer.close()
        if fetch_task is not None:
            try:
                await fetch_task
            except asyncio.CancelledError:
                pass
    return gate.get_all_checkpoints()


def _failing_connection(execute_side_effect=None, commit_side_effect=None):
    connection = MagicMock()
    if execute_side_effect is not None:
        connection.execute.side_effect = execute_side_effect
    if commit_side_effect is not None:
        connection.commit.side_effect = commit_side_effect
    return connection


@pytest.mark.parametrize("stage", ["execute", "refresh", "commit"])
def test_failed_event_blocks_checkpoint_even_when_later_events_succeed(relay_factory, stage, caplog):
    """
    Sequence: good(1), bad(2, write fails), good(3).
    With on_error="ignore" consumption continues, but the failed event must never
    be acknowledged: the checkpoint stays at the last safe position before it.
    """
    relay = relay_factory(on_error="ignore")
    relay.checkpoint_gate = GatedCheckPointer(MemoryCheckPointer(name="relay-gate"))

    write_error = OperationalError("query", {}, Exception("write failed"))
    if stage == "execute":
        # good-1 execute ok; bad execute fails; good-2 execute ok.
        relay.connection = _failing_connection(execute_side_effect=[None, write_error, None])
    elif stage == "refresh":
        relay.cratedb_table = '"testdrive"."demo"'
        # good-1: execute + REFRESH ok; bad: execute ok then REFRESH fails; good-2: both ok.
        relay.connection = _failing_connection(execute_side_effect=[None, None, None, write_error, None, None])
    else:
        # good-1 commits ok; bad event commit fails; good-2 commit recovers.
        relay.connection = _failing_connection(commit_side_effect=[None, write_error, None])

    queue = [
        {"id": "good-1"},
        _checkpoint(1),
        {"id": "bad"},
        _checkpoint(2),
        {"id": "good-2"},
        _checkpoint(3),
    ]

    checkpoints = asyncio.run(_drive_consumer(relay, queue))
    assert checkpoints[SHARD]["sequence"] is None
    # The failed event was rolled back; consumption continued afterwards.
    relay.connection.rollback.assert_called_once_with()
    assert relay.progress_bar.update.call_count == 2
    assert "Executing query failed" in caplog.text


def test_all_successful_events_advance_checkpoint_to_the_end(relay_factory):
    relay = relay_factory(on_error="ignore")
    relay.checkpoint_gate = GatedCheckPointer(MemoryCheckPointer(name="relay-gate"))

    queue = [
        {"id": "good-1"},
        _checkpoint(1),
        {"id": "good-2"},
        _checkpoint(2),
        {"id": "good-3"},
        _checkpoint(3),
    ]

    checkpoints = asyncio.run(_drive_consumer(relay, queue))
    assert checkpoints[SHARD]["sequence"] == "3"
    relay.connection.rollback.assert_not_called()
    assert relay.progress_bar.update.call_count == 3


def test_failed_event_with_raise_policy_propagates_and_keeps_checkpoint(relay_factory):
    """
    on_error="raise": the write error still propagates, and because the shard is
    frozen, nothing past the failed event is checkpointed before it bubbles up.
    """
    relay = relay_factory(on_error="raise")
    relay.checkpoint_gate = GatedCheckPointer(MemoryCheckPointer(name="relay-gate"))
    write_error = OperationalError("query", {}, Exception("write failed"))
    # good-1 execute ok; bad execute fails and aborts consumption.
    relay.connection = _failing_connection(execute_side_effect=[None, write_error])

    queue = [
        {"id": "good-1"},
        _checkpoint(1),
        {"id": "bad"},
        _checkpoint(2),
    ]

    checkpoints = asyncio.run(_drive_consumer(relay, queue, expect_error=OperationalError))
    assert checkpoints[SHARD]["sequence"] is None
    relay.connection.rollback.assert_not_called()


def test_connection_error_always_propagates_even_with_ignore_policy(relay_factory):
    """
    An unreachable database is not a per-event failure and must propagate
    regardless of on_error, while still freezing the checkpoint.
    """
    relay = relay_factory(on_error="ignore")
    relay.checkpoint_gate = GatedCheckPointer(MemoryCheckPointer(name="relay-gate"))
    connection_error = OperationalError("query", {}, CrateConnectionError("No more Servers available"))
    relay.connection = _failing_connection(execute_side_effect=[None, connection_error])

    queue = [
        {"id": "good-1"},
        _checkpoint(1),
        {"id": "bad"},
        _checkpoint(2),
    ]

    checkpoints = asyncio.run(_drive_consumer(relay, queue, expect_error=OperationalError))
    assert checkpoints[SHARD]["sequence"] is None
    relay.connection.rollback.assert_not_called()


def test_process_event_freezes_all_owned_shards_without_shard_context(relay_factory):
    """
    The handler receives plain payloads without shard metadata, so a failed
    write conservatively freezes every owned shard. Verify the relay actually
    drives the gate on failure and later checkpoint calls are suppressed.
    """
    relay = relay_factory(on_error="ignore")
    inner = MemoryCheckPointer(name="two-shards")
    gate = GatedCheckPointer(inner)
    relay.checkpoint_gate = gate
    relay.connection = _failing_connection(execute_side_effect=[OperationalError("q", {}, Exception("boom"))])

    async def scenario():
        await gate.allocate("shard-a")
        await gate.allocate("shard-b")
        await gate.checkpoint("shard-a", "1")
        await gate.checkpoint("shard-b", "1")

        # The write for an event fails; the relay freezes all owned shards.
        relay.process_event({"id": "bad"})
        assert gate.is_frozen("shard-a") and gate.is_frozen("shard-b")

        await gate.checkpoint("shard-a", "2")
        await gate.checkpoint("shard-b", "2")
        return inner.get_all_checkpoints()

    checkpoints = asyncio.run(scenario())
    # Neither shard advances past its last safe position.
    assert checkpoints["shard-a"]["sequence"] == "1"
    assert checkpoints["shard-b"]["sequence"] == "1"
    relay.connection.rollback.assert_called_once_with()


def test_no_checkpointer_path_has_no_gate_and_keeps_working(relay_factory):
    """Without a configured checkpointer the relay must not gate anything."""
    relay = relay_factory(on_error="ignore")
    assert relay.checkpoint_gate is None

    # A write failure is handled by the existing policy; the gate attribute is
    # simply absent and must not interfere.
    relay.connection = _failing_connection(execute_side_effect=[OperationalError("q", {}, Exception("boom")), None])
    relay.process_event({"id": "bad"})
    relay.process_event({"id": "good"})
    relay.connection.rollback.assert_called_once_with()
    assert relay.progress_bar.update.call_count == 1


def test_gate_is_installed_around_real_checkpointer(mocker):
    """
    _setup_checkpointer wraps whichever backend is selected, so the consumer
    always talks to the gating wrapper instead of the raw checkpointer.
    """
    mocker.patch("cratedb_toolkit.io.kinesis.relay.KinesisAdapterBase.factory")
    mocker.patch("cratedb_toolkit.io.kinesis.relay.DatabaseAdapter")

    relay = KinesisRelay("kinesis+dms://localhost/demo", "crate://localhost/testdrive/demo")
    # A real adapter passes the isinstance(KinesisStreamAdapter) guard in setup;
    # build one without invoking its boto3-touching __init__.
    adapter = KinesisStreamAdapter.__new__(KinesisStreamAdapter)
    adapter.checkpointer_type = "memory"
    adapter.checkpointer_name = "demo"
    adapter.checkpointer_schema = "ext"
    adapter.set_checkpointer = MagicMock()
    relay.kinesis_adapter = adapter

    relay._setup_checkpointer()

    assert isinstance(relay.checkpoint_gate, GatedCheckPointer)
    installed = adapter.set_checkpointer.call_args.args[0]
    assert installed is relay.checkpoint_gate
    assert isinstance(installed._checkpointer, MemoryCheckPointer)


def test_no_checkpointer_configuration_leaves_gate_absent(mocker):
    mocker.patch("cratedb_toolkit.io.kinesis.relay.KinesisAdapterBase.factory")
    mocker.patch("cratedb_toolkit.io.kinesis.relay.DatabaseAdapter")

    relay = KinesisRelay("kinesis+dms://localhost/demo", "crate://localhost/testdrive/demo")
    adapter = KinesisStreamAdapter.__new__(KinesisStreamAdapter)
    adapter.checkpointer_type = None
    adapter.start = "earliest"
    adapter.set_checkpointer = MagicMock()
    relay.kinesis_adapter = adapter

    relay._setup_checkpointer()

    assert relay.checkpoint_gate is None
    adapter.set_checkpointer.assert_not_called()
