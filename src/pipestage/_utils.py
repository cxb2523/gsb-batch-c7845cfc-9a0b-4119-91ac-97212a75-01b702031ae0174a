from __future__ import annotations

import inspect
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable
from typing import Any, TypeVar

T = TypeVar("T")


async def _coerce(fn: Callable[..., Any], *args: Any) -> Any:
    # supports both sync and async callables without requiring the caller to know which
    result = fn(*args)
    if inspect.isawaitable(result):
        return await result
    return result


async def to_async_iter(source: Iterable[T] | AsyncIterable[T]) -> AsyncIterator[T]:
    if isinstance(source, AsyncIterable):
        async for item in source:
            yield item
    else:
        for item in source:
            yield item


async def _try_aclose(it: Any) -> None:
    # cancels any in-flight tasks in an upstream concurrent stage when a
    # terminal short-circuits before the source is exhausted
    aclose = getattr(it, "aclose", None)
    if aclose is not None:
        await aclose()
