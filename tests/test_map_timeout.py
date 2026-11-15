"""Per-item timeout and error-isolation (on_error) tests for Stream.map."""

import asyncio
import random

import pytest

from pipestage import stream


async def test_timeout_replaces_slow_items_ordered():
    """Timed-out items are replaced; other results are complete and in order."""

    async def fetch(x: int):
        if x in (2, 5):
            await asyncio.sleep(5)  # stuck request
        else:
            await asyncio.sleep(0.001)
        return x * 10

    def on_error(exc: BaseException) -> str:
        assert isinstance(exc, TimeoutError)
        return "TIMEOUT"

    result = await (
        stream(range(8))
        .map(fetch, concurrency=4, ordered=True, timeout=0.05, on_error=on_error)
        .collect()
    )
    assert result == [0, 10, "TIMEOUT", 30, 40, "TIMEOUT", 60, 70]


async def test_timeout_replaces_slow_items_unordered():
    """ordered=False: replacements are emitted too, all items accounted for."""

    async def fetch(x: int):
        if x in (1, 6):
            await asyncio.sleep(5)
        else:
            await asyncio.sleep(random.uniform(0, 0.01))
        return x

    result = await (
        stream(range(8))
        .map(
            fetch,
            concurrency=4,
            ordered=False,
            timeout=0.05,
            on_error=lambda exc: "TIMEOUT",
        )
        .collect()
    )
    assert result.count("TIMEOUT") == 2
    assert sorted(v for v in result if v != "TIMEOUT") == [0, 2, 3, 4, 5, 7]


async def test_on_error_isolates_regular_exceptions():
    """Non-timeout failures are also replaced by on_error, batch runs to the end."""

    async def maybe_fail(x: int) -> int:
        await asyncio.sleep(0.001)
        if x % 3 == 0:
            raise ValueError(f"bad:{x}")
        return x

    seen: list[BaseException] = []

    def on_error(exc: BaseException) -> int:
        seen.append(exc)
        return -1

    result = await (
        stream(range(10))
        .map(maybe_fail, concurrency=3, on_error=on_error)
        .collect()
    )
    assert result == [-1, 1, 2, -1, 4, 5, -1, 7, 8, -1]
    assert [str(e) for e in seen] == ["bad:0", "bad:3", "bad:6", "bad:9"]


async def test_serial_timeout_and_on_error():
    """concurrency=1 path supports timeout + on_error too."""

    async def fetch(x: int) -> int:
        if x == 1:
            await asyncio.sleep(5)
        return x

    result = await (
        stream(range(3))
        .map(fetch, timeout=0.05, on_error=lambda exc: "T")
        .collect()
    )
    assert result == [0, "T", 2]


async def test_on_error_raising_propagates_fail_fast():
    """If on_error itself raises, the exception propagates and the batch fails."""

    async def fail(x: int) -> int:
        raise ValueError("original failure")

    def on_error(exc: BaseException) -> int:
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError, match="handler failed"):
        await stream([1, 2, 3]).map(fail, concurrency=2, on_error=on_error).collect()


async def test_timeout_without_on_error_fails_fast():
    """A timeout with no on_error keeps the existing fail-fast behavior."""

    async def hang(x: int) -> int:
        await asyncio.sleep(5)
        return x

    with pytest.raises(TimeoutError):
        await stream(range(4)).map(hang, concurrency=2, timeout=0.05).collect()


async def test_timeout_not_triggered_produces_no_replacements():
    """Boundary: every call finishes well under the timeout -> on_error never runs."""
    calls: list[BaseException] = []

    async def quick(x: int) -> int:
        await asyncio.sleep(0.005)
        return x * 2

    def on_error(exc: BaseException) -> str:
        calls.append(exc)
        return "REPLACEMENT"

    result = await (
        stream(range(10))
        .map(quick, concurrency=4, timeout=2.0, on_error=on_error)
        .collect()
    )
    assert result == [x * 2 for x in range(10)]
    assert calls == []


async def test_simultaneous_timeouts_respect_concurrency_limit():
    """Many items timing out at once must still respect the concurrency cap."""
    limit = 3
    running = 0
    peak = 0

    async def hang(x: int) -> int:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            await asyncio.sleep(5)
        finally:
            running -= 1
        return x

    result = await (
        stream(range(9))
        .map(hang, concurrency=limit, timeout=0.05, on_error=lambda exc: "T")
        .collect()
    )
    assert result == ["T"] * 9
    assert peak <= limit


async def test_short_circuit_leaves_no_dangling_tasks():
    """first() must cancel in-flight timed-out work; nothing emits downstream."""
    emitted: list[int] = []

    async def fetch(x: int) -> int:
        if x == 0:
            return x
        await asyncio.sleep(5)
        emitted.append(x)  # must never run for cancelled tasks
        return x

    s = stream(range(6)).map(
        fetch, concurrency=3, ordered=True, timeout=10, on_error=lambda exc: "T"
    )
    assert await s.first() == 0
    await asyncio.sleep(0.1)  # let cancellations settle
    assert emitted == []
    others = [
        t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()
    ]
    assert others == []


@pytest.mark.parametrize("bad", [0, -1, -0.5, 0.0, "1", object(), float("nan")])
def test_invalid_timeout_raises_value_error(bad):
    """Invalid timeout values are rejected at construction time."""
    with pytest.raises(ValueError):
        stream([1]).map(lambda x: x, timeout=bad)


def test_timeout_none_is_valid():
    s = stream([1]).map(lambda x: x, timeout=None)
    assert s is not None


async def test_crawler_batch_acceptance_sample():
    """Simulated crawl batch: random delays, occasional 500s, stuck requests.

    With timeout + on_error the batch completes and bad entries are replaced;
    without them the same batch fails fast exactly as before.
    """
    rng = random.Random(42)
    delays = {i: rng.uniform(0.001, 0.02) for i in range(30)}
    flaky = {3, 11, 27}  # raise RuntimeError
    stuck = {7, 19}  # hang until the per-item timeout fires

    async def crawl(x: int) -> str:
        if x in stuck:
            await asyncio.sleep(5)
        await asyncio.sleep(delays[x])
        if x in flaky:
            raise RuntimeError(f"http 500:{x}")
        return f"page:{x}"

    def on_error(exc: BaseException) -> str:
        return f"skipped:{type(exc).__name__}"

    result = await (
        stream(range(30))
        .map(crawl, concurrency=6, ordered=True, timeout=0.2, on_error=on_error)
        .collect()
    )

    assert len(result) == 30
    for x in range(30):
        if x in flaky or x in stuck:
            assert result[x].startswith("skipped:"), (x, result[x])
        else:
            assert result[x] == f"page:{x}"
    assert result[7] == result[19] == "skipped:TimeoutError"
    assert result[3] == result[11] == result[27] == "skipped:RuntimeError"

    # New parameters off -> behavior is identical to before: fail-fast.
    with pytest.raises(RuntimeError, match="http 500:3"):
        await stream(range(30)).map(crawl, concurrency=6, ordered=True).collect()
