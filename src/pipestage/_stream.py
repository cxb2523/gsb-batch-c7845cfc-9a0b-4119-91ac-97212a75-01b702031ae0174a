from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Callable
from typing import Any, Generic, TypeVar

from ._ops import batch_stage, filter_stage, flat_map_stage, map_stage, take_stage
from ._utils import _coerce, _try_aclose

T = TypeVar("T")


def _check_positive(name: str, value: int) -> None:
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")


class Stream(Generic[T]):

    __slots__ = ("_source",)

    def __init__(self, source: AsyncIterable[T]) -> None:
        self._source = source

    def map(
        self,
        fn: Callable[..., Any],
        *,
        concurrency: int = 1,
        ordered: bool = True,
    ) -> Stream[Any]:
        _check_positive("concurrency", concurrency)
        return Stream(map_stage(self._source, fn, concurrency, ordered))

    def filter(
        self,
        pred: Callable[..., Any],
        *,
        concurrency: int = 1,
        ordered: bool = True,
    ) -> Stream[T]:
        _check_positive("concurrency", concurrency)
        return Stream(filter_stage(self._source, pred, concurrency, ordered))

    def flat_map(
        self,
        fn: Callable[..., Any],
        *,
        concurrency: int = 1,
        ordered: bool = True,
    ) -> Stream[Any]:
        _check_positive("concurrency", concurrency)
        return Stream(flat_map_stage(self._source, fn, concurrency, ordered))

    def batch(self, size: int) -> Stream[list[T]]:
        _check_positive("batch size", size)
        return Stream(batch_stage(self._source, size))

    def take(self, n: int) -> Stream[T]:
        if n < 0:
            raise ValueError(f"n must be >= 0, got {n}")
        return Stream(take_stage(self._source, n))

    async def collect(self) -> list[T]:
        return [item async for item in self._source]

    async def for_each(
        self,
        fn: Callable[..., Any],
        *,
        concurrency: int = 1,
        ordered: bool = True,
    ) -> None:
        _check_positive("concurrency", concurrency)
        async for _ in map_stage(self._source, fn, concurrency, ordered):
            pass

    async def first(self) -> T:
        it = self._source.__aiter__()
        try:
            return await it.__anext__()
        finally:
            await _try_aclose(it)

    async def last(self) -> T:
        it = self._source.__aiter__()
        result = await it.__anext__()
        while True:
            try:
                result = await it.__anext__()
            except StopAsyncIteration:
                return result

    async def count(self) -> int:
        n = 0
        async for _ in self._source:
            n += 1
        return n

    async def reduce(self, fn: Callable[..., Any], initial: Any) -> Any:
        acc = initial
        async for item in self._source:
            acc = await _coerce(fn, acc, item)
        return acc

    async def any(self, pred: Callable[..., Any]) -> bool:
        it = self._source.__aiter__()
        try:
            async for item in it:
                if await _coerce(pred, item):
                    return True
            return False
        finally:
            await _try_aclose(it)

    async def all(self, pred: Callable[..., Any]) -> bool:
        it = self._source.__aiter__()
        try:
            async for item in it:
                if not await _coerce(pred, item):
                    return False
            return True
        finally:
            await _try_aclose(it)

    def __aiter__(self) -> AsyncIterator[T]:
        return self._source.__aiter__()

    async def aclose(self) -> None:
        await _try_aclose(self._source)
