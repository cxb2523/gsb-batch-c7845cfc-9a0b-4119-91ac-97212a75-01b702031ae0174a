"""采集批次接口：POST /collect/batches。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.collect import (
    CursorError,
    CursorOutOfBoundsError,
    decode_cursor,
    encode_cursor,
    run_batch,
)
from app.schemas import CollectBatchRequest, CollectBatchResponse

router = APIRouter()

MIN_LIMIT = 1
MAX_LIMIT = 100


def _bad_request(message: str) -> HTTPException:
    """构造统一的 400 响应。"""

    return HTTPException(status_code=400, detail=message)


@router.post(
    "/collect/batches",
    tags=["collect"],
    response_model=CollectBatchResponse,
    responses={400: {"description": "请求参数非法"}},
)
def create_collect_batch(
    payload: CollectBatchRequest,
) -> CollectBatchResponse:
    # --- source 校验 -----------------------------------------------------
    if payload.source is None:
        raise _bad_request("source 为必填项，且必须是非空字符串")
    if not isinstance(payload.source, str) or not payload.source.strip():
        raise _bad_request("source 必须是非空字符串")
    source = payload.source.strip()

    # --- limit 校验（1..100 的整数，布尔值不算整数） ---------------------
    if payload.limit is None:
        raise _bad_request("limit 为必填项，且必须是 1 到 100 之间的整数")
    if isinstance(payload.limit, bool) or not isinstance(payload.limit, int):
        raise _bad_request("limit 必须是 1 到 100 之间的整数")
    if not MIN_LIMIT <= payload.limit <= MAX_LIMIT:
        raise _bad_request("limit 必须是 1 到 100 之间的整数")
    limit = payload.limit

    # --- cursor 校验（缺省表示从头取） ------------------------------------
    if payload.cursor is None:
        start_offset = 0
    else:
        if not isinstance(payload.cursor, str):
            raise _bad_request("cursor 必须是字符串，缺省时请传 null 或省略该字段")
        try:
            start_offset = decode_cursor(payload.cursor)
        except CursorError as exc:
            raise _bad_request(str(exc)) from exc

    # --- 取数 ------------------------------------------------------------
    try:
        return run_batch(source=source, start_offset=start_offset, limit=limit)
    except CursorOutOfBoundsError as exc:
        # 游标越界属于调用方错误，返回 400。
        raise _bad_request(str(exc)) from exc
    except CursorError as exc:  # 防御性兜底
        raise _bad_request(str(exc)) from exc


# 供路由层以外的模块复用游标编码（例如测试中构造游标）。
__all__ = ["router", "encode_cursor"]
