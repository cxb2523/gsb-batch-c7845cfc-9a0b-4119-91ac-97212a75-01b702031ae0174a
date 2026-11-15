"""take, first, last, count, reduce, any, all -- v0.3.0 terminal shortcuts."""

import asyncio

import pytest

from pipestage import stream


async def test_take_basic():
    assert await stream(range(10)).take(3).collect() == [0, 1, 2]


async def test_take_more_than_available():
    assert await stream([1, 2, 3]).take(10).collect() == [1, 2, 3]


async def test_take_zero():
    assert await stream([1, 2, 3]).take(0).collect() == []


async def test_take_negative_raises():
    with pytest.raises(ValueError):
        stream([1, 2, 3]).take(-1)


async def test_take_chains_with_map():
    result = await stream(range(100)).map(lambda x: x * 2).take(3).collect()
    assert result == [0, 2, 4]


async def test_take_cancels_inflight_tasks():
    cancelled: list[int] = []

    async def worker(x: int) -> int:
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            cancelled.append(x)
            raise
        return x

    result = await stream(range(10)).map(worker, concurrency=3).take(1).collect()

    await asyncio.sleep(0.05)
    assert result == [0]
    assert len(cancelled) > 0


async def test_first():
    assert await stream([1, 2, 3]).first() == 1


async def test_first_empty_raises():
    with pytest.raises(StopAsyncIteration):
        await stream([]).first()


async def test_first_cancels_inflight_tasks():
    cancelled: list[int] = []

    async def worker(x: int) -> int:
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            cancelled.append(x)
            raise
        return x

    result = await stream(range(10)).map(worker, concurrency=3).first()

    await asyncio.sleep(0.05)
    assert result == 0
    assert len(cancelled) > 0


async def test_last():
    assert await stream([1, 2, 3]).last() == 3


async def test_last_empty_raises():
    with pytest.raises(StopAsyncIteration):
        await stream([]).last()


async def test_count():
    assert await stream(range(7)).count() == 7


async def test_count_empty():
    assert await stream([]).count() == 0


async def test_reduce_sum():
    result = await stream([1, 2, 3, 4]).reduce(lambda acc, x: acc + x, 0)
    assert result == 10


async def test_reduce_async_fn():
    async def add(acc: int, x: int) -> int:
        return acc + x

    result = await stream([1, 2, 3]).reduce(add, 0)
    assert result == 6


async def test_reduce_empty_returns_initial():
    result = await stream([]).reduce(lambda acc, x: acc + x, 42)
    assert result == 42


async def test_any_true():
    assert await stream([1, 2, 3]).any(lambda x: x == 2) is True


async def test_any_false():
    assert await stream([1, 2, 3]).any(lambda x: x == 99) is False


async def test_any_empty():
    assert await stream([]).any(lambda x: True) is False


async def test_any_short_circuits():
    seen: list[int] = []

    def pred(x: int) -> bool:
        seen.append(x)
        return x == 2

    assert await stream([1, 2, 3, 4, 5]).any(pred) is True
    assert seen == [1, 2]


async def test_all_true():
    assert await stream([2, 4, 6]).all(lambda x: x % 2 == 0) is True


async def test_all_false():
    assert await stream([2, 4, 5]).all(lambda x: x % 2 == 0) is False


async def test_all_empty():
    assert await stream([]).all(lambda x: False) is True


async def test_all_short_circuits():
    seen: list[int] = []

    def pred(x: int) -> bool:
        seen.append(x)
        return x != 3

    assert await stream([1, 2, 3, 4, 5]).all(pred) is False
    assert seen == [1, 2, 3]
