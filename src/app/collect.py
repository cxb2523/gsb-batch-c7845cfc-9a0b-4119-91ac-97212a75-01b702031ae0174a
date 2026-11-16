"""可续取采集批次的数据源接入与批次取数逻辑。

设计要点：

- 数据源通过「逐条取数函数」接入，签名为 ``fetcher(source, offset)``：

  * 返回该偏移位置的记录（dict）；
  * 返回 ``None`` 表示数据已经取完；
  * 抛异常表示数据源中途出错。

- 取数函数可通过 :func:`set_record_fetcher` 替换，默认实现
  :func:`default_fetch_record` 只读取内存中的样例记录，不发起任何网络请求。
- 游标是不透明的 base64 字符串，内部仅编码一个整数偏移，调用方不需要、
  也不应该自行解析；解析或越界时抛出 :class:`CursorError`。
- 每次批次调用的偏移、已取记录、计数全部是函数内的局部状态，不按 source
  保存任何共享游标或计数器，因此同一 source 的并发请求之间不会互相污染。
"""

from __future__ import annotations

import base64
import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

from app.schemas import CollectBatchResponse

# 游标格式：base64url("v1:<offset>")，去掉末尾的 "=" 填充。
_CURSOR_VERSION = "v1"
_CURSOR_CHARSET = re.compile(r"^[A-Za-z0-9_-]+$")


class RecordFetcher(Protocol):
    """逐条取数函数的协议。"""

    def __call__(self, source: str, offset: int) -> dict[str, Any] | None:
        """返回 ``source`` 在 ``offset`` 位置的记录，取完返回 ``None``。"""


class CursorError(ValueError):
    """游标无法解析（格式非法或偏移为负）。"""


class CursorOutOfBoundsError(CursorError):
    """游标可以解析，但指向的位置之后没有任何可取记录。"""

    def __init__(self, offset: int) -> None:
        self.offset = offset
        super().__init__(f"cursor 越界：偏移 {offset} 之后没有可取记录")


# ---------------------------------------------------------------------------
# 内存样例数据（默认数据源）
# ---------------------------------------------------------------------------
SAMPLE_RECORDS: dict[str, tuple[dict[str, Any], ...]] = {
    "demo_customers": tuple(
        {
            "id": index,
            "name": f"客户-{index:03d}",
            "source": "demo_customers",
            "active": index % 2 == 0,
        }
        for index in range(25)
    ),
    "demo_orders": tuple(
        {
            "id": 1000 + index,
            "order_no": f"ORD-{1000 + index}",
            "amount": round(index * 9.9, 2),
            "source": "demo_orders",
        }
        for index in range(13)
    ),
}


def default_fetch_record(source: str, offset: int) -> dict[str, Any] | None:
    """默认取数函数：按 source 读取内存样例记录。

    未知 source、负偏移或越过末尾都返回 ``None``，不抛异常。
    """

    records = SAMPLE_RECORDS.get(source)
    if records is None or offset < 0 or offset >= len(records):
        return None
    return records[offset]


# ---------------------------------------------------------------------------
# 可替换取数函数的注册表
# ---------------------------------------------------------------------------
_fetcher_lock = threading.RLock()
_record_fetcher: RecordFetcher = default_fetch_record


def set_record_fetcher(fetcher: RecordFetcher) -> None:
    """替换全局取数函数（例如接入真实数据源或测试桩）。"""

    with _fetcher_lock:
        global _record_fetcher
        _record_fetcher = fetcher


def get_record_fetcher() -> RecordFetcher:
    """获取当前生效的取数函数。"""

    with _fetcher_lock:
        return _record_fetcher


def reset_record_fetcher() -> None:
    """恢复为默认的内存样例取数函数。"""

    set_record_fetcher(default_fetch_record)


@contextmanager
def use_record_fetcher(fetcher: RecordFetcher) -> Iterator[None]:
    """临时替换取数函数，退出上下文时自动还原（主要供测试使用）。"""

    previous = get_record_fetcher()
    set_record_fetcher(fetcher)
    try:
        yield
    finally:
        set_record_fetcher(previous)


# ---------------------------------------------------------------------------
# 游标编解码
# ---------------------------------------------------------------------------
def encode_cursor(offset: int) -> str:
    """把整数偏移编码为不透明游标字符串。"""

    if offset < 0:
        raise CursorError("cursor 无法解析：偏移不能为负数")
    raw = f"{_CURSOR_VERSION}:{offset}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str) -> int:
    """把游标字符串解码为整数偏移，非法游标抛 :class:`CursorError`。"""

    if not isinstance(token, str) or not token or not _CURSOR_CHARSET.fullmatch(token):
        raise CursorError("cursor 无法解析：格式不合法")

    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        text = raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise CursorError("cursor 无法解析：内容不合法") from exc

    version, separator, value = text.partition(":")
    if version != _CURSOR_VERSION or not separator:
        raise CursorError("cursor 无法解析：版本或内容不合法")

    try:
        offset = int(value)
    except ValueError as exc:
        raise CursorError("cursor 无法解析：偏移不是整数") from exc
    if offset < 0:
        raise CursorError("cursor 无法解析：偏移不能为负数")
    return offset


# ---------------------------------------------------------------------------
# 批次取数
# ---------------------------------------------------------------------------
def run_batch(source: str, start_offset: int, limit: int) -> CollectBatchResponse:
    """按游标拉取一批记录。

    取数函数抛异常时本函数不抛出，而是转成响应状态：

    - 已取到至少一条记录 -> ``partial``，``next_cursor`` 指向出错位置；
    - 一条都没取到 -> ``failed``，``next_cursor`` 指向本次起始位置，
      调用方可原样重试。

    正常取完一批（条数等于 limit）时会额外探测一次下一条记录，用来判断
    ``has_more``；若探测本身报错，同样降级为 ``partial``。
    """

    fetcher = get_record_fetcher()
    batch_id = uuid.uuid4().hex
    items: list[dict[str, Any]] = []
    offset = start_offset

    def respond(
        status: str, next_cursor: str | None, has_more: bool
    ) -> CollectBatchResponse:
        return CollectBatchResponse(
            batch_id=batch_id,
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
            fetched_count=len(items),
            status=status,  # type: ignore[arg-type]
        )

    try:
        record = fetcher(source, offset)
        if record is None:
            if start_offset == 0:
                # 从头取就没有记录（未知 source 或空数据源）属于正常空结果，
                # 不是游标越界；只有显式传入的游标越过末尾才算 400。
                return respond("ok", None, False)
            raise CursorOutOfBoundsError(offset)
        items.append(record)
        offset += 1

        while len(items) < limit:
            record = fetcher(source, offset)
            if record is None:
                break
            items.append(record)
            offset += 1
    except CursorOutOfBoundsError:
        raise
    except Exception:
        # 数据源中途出错：不得让异常冒泡成 500。
        if items:
            return respond("partial", encode_cursor(offset), True)
        return respond("failed", encode_cursor(start_offset), True)

    # 没取满 limit 就遇到 None，说明数据自然取完。
    if len(items) < limit:
        return respond("ok", None, False)

    # 已取满一批，探测下一条是否存在以决定 has_more / next_cursor。
    try:
        probe = fetcher(source, offset)
    except Exception:
        return respond("partial", encode_cursor(offset), True)

    if probe is None:
        return respond("ok", None, False)
    return respond("ok", encode_cursor(offset), True)
