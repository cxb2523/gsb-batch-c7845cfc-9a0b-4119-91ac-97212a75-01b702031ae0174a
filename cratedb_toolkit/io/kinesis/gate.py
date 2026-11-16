import logging
import typing as t

logger = logging.getLogger(__name__)


class GatedCheckPointer:
    """
    Checkpointer wrapper ensuring a checkpoint never advances past a failed event.

    The async-kinesis ``Consumer`` treats a handler invocation as implicitly
    acknowledged once control returns to it and then asks the checkpointer to
    persist the sequence number of the processed records. That mechanism is
    handler-agnostic: it cannot tell whether an event was written to CrateDB
    successfully, so a failed write would still be checkpointed and skipped
    permanently on the next restart.

    This wrapper sits between the consumer and the real checkpointer. The relay
    reports every failed write via :meth:`freeze`, which blocks all subsequent
    ``checkpoint()`` calls for the affected shard. A checkpoint therefore only
    ever reflects a prefix of events that were written *and committed*
    successfully; a frozen shard simply replays the failed event (and everything
    behind it) on the next start instead of opening a data gap.

    Checkpointing is a per-shard concept, so freeze state is tracked per shard.
    A write failure is reported without shard context (the consumer hands plain
    record payloads to the handler); freezing every owned shard is conservative
    but always safe, since replaying a few already-processed records only trades
    a little extra work for the guarantee of never skipping an event.
    """

    def __init__(self, checkpointer: t.Any):
        self._checkpointer = checkpointer
        # Shards currently owned by the wrapped checkpointer (seen via allocate()).
        self._allocated_shards: t.Set[str] = set()
        # Shards whose progress must no longer advance because a write failed.
        self._frozen_shards: t.Set[str] = set()

    @property
    def frozen_shards(self) -> t.Set[str]:
        return set(self._frozen_shards)

    def freeze(self, shard_id: t.Optional[str] = None) -> None:
        """
        Block future checkpoint advances after an event could not be written.

        With a known ``shard_id`` only that shard is frozen. Without one, every
        shard currently owned by the wrapped checkpointer is frozen, which is
        safe even when more than one shard is consumed concurrently.
        """
        if shard_id is not None:
            self._frozen_shards.add(shard_id)
            logger.warning(f"Checkpoint advances frozen for shard {shard_id!r} after failed write")
            return

        self._frozen_shards.update(self._allocated_shards)
        if self._allocated_shards:
            logger.warning(f"Checkpoint advances frozen for shards {sorted(self._frozen_shards)!r} after failed write")
        else:
            logger.warning("Checkpoint advances frozen after failed write (no shard allocated yet)")

    def is_frozen(self, shard_id: str) -> bool:
        return shard_id in self._frozen_shards

    async def allocate(self, shard_id: str):
        result = await self._checkpointer.allocate(shard_id)
        if result[0]:
            self._allocated_shards.add(shard_id)
        return result

    async def deallocate(self, shard_id: str) -> None:
        await self._checkpointer.deallocate(shard_id)
        self._allocated_shards.discard(shard_id)
        self._frozen_shards.discard(shard_id)

    async def checkpoint(self, shard_id: str, sequence_number: str) -> None:
        if shard_id in self._frozen_shards:
            logger.warning(
                f"Suppressing checkpoint {shard_id}@{sequence_number}: "
                f"an earlier event failed to write, keeping last safe position"
            )
            return
        await self._checkpointer.checkpoint(shard_id, sequence_number)

    async def close(self) -> None:
        await self._checkpointer.close()

    def get_all_checkpoints(self):
        return self._checkpointer.get_all_checkpoints()

    def is_allocated(self, shard_id: str) -> bool:
        return self._checkpointer.is_allocated(shard_id)

    def get_ref(self) -> str:
        return self._checkpointer.get_ref()

    def bind_metrics(self, *args, **kwargs) -> None:
        if hasattr(self._checkpointer, "bind_metrics"):
            self._checkpointer.bind_metrics(*args, **kwargs)

    def __getattr__(self, item):
        # Forward any checkpointer-specific attribute we do not need to gate.
        return getattr(self._checkpointer, item)
