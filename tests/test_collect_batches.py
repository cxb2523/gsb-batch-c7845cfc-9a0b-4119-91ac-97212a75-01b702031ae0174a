"""采集批次接口 POST /api/v1/collect/batches 的测试。

全部使用自造的内存样例数据和取数桩，不依赖任何外部服务。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.collect import (
    SAMPLE_RECORDS,
    CursorError,
    decode_cursor,
    encode_cursor,
    use_record_fetcher,
)
from app.main import app

ENDPOINT = "/api/v1/collect/batches"


def _record(offset: int, **extra: Any) -> dict[str, Any]:
    return {"id": offset, **extra}


# ---------------------------------------------------------------------------
# 正常分页
# ---------------------------------------------------------------------------
def test_pagination_walks_all_records_until_null_cursor() -> None:
    total = len(SAMPLE_RECORDS["demo_customers"])
    client = TestClient(app)

    cursor: str | None = None
    seen: list[dict[str, Any]] = []
    batch_sizes = []

    while True:
        payload: dict[str, Any] = {"source": "demo_customers", "limit": 10}
        if cursor is not None:
            payload["cursor"] = cursor
        response = client.post(ENDPOINT, json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "ok"
        assert data["fetched_count"] == len(data["items"])
        assert isinstance(data["batch_id"], str) and data["batch_id"]
        batch_sizes.append(data["fetched_count"])
        seen.extend(data["items"])

        cursor = data["next_cursor"]
        if not data["has_more"]:
            assert cursor is None
            break
        assert cursor is not None
        # 服务端下发的游标必须能被自身解析。
        assert decode_cursor(cursor) == len(seen)

    assert batch_sizes == [10, 10, 5]
    assert len(seen) == total
    assert [item["id"] for item in seen] == list(range(total))


def test_default_cursor_starts_from_beginning_and_limit_1() -> None:
    client = TestClient(app)

    response = client.post(
        ENDPOINT, json={"source": "demo_orders", "limit": 1}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["fetched_count"] == 1
    assert data["items"][0] == SAMPLE_RECORDS["demo_orders"][0]
    assert data["has_more"] is True
    assert decode_cursor(data["next_cursor"]) == 1


def test_exact_fit_final_batch_has_null_cursor() -> None:
    # demo_orders 共 13 条，limit=13 时一批取完且 has_more 为 False。
    client = TestClient(app)
    response = client.post(
        ENDPOINT, json={"source": "demo_orders", "limit": 13}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["fetched_count"] == 13
    assert data["has_more"] is False
    assert data["next_cursor"] is None


def test_unknown_source_is_empty_ok() -> None:
    client = TestClient(app)
    response = client.post(
        ENDPOINT, json={"source": "no-such-source", "limit": 10}
    )
    assert response.status_code == 200
    data = response.json()
    assert data == {
        "batch_id": data["batch_id"],
        "items": [],
        "next_cursor": None,
        "has_more": False,
        "fetched_count": 0,
        "status": "ok",
    }


# ---------------------------------------------------------------------------
# 中途出错：partial / failed
# ---------------------------------------------------------------------------
class _FailingFetcher:
    """从 fail_at 偏移开始持续抛错的取数桩。"""

    def __init__(self, fail_at: int) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[str, int]] = []

    def __call__(self, source: str, offset: int) -> dict[str, Any] | None:
        self.calls.append((source, offset))
        if offset >= self.fail_at:
            raise RuntimeError(f"数据源在偏移 {offset} 处故障")
        return _record(offset)


def test_mid_batch_error_returns_partial_and_keeps_records() -> None:
    client = TestClient(app)
    with use_record_fetcher(_FailingFetcher(fail_at=7)):
        response = client.post(
            ENDPOINT, json={"source": "fixture", "limit": 10}
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial"
    assert data["fetched_count"] == 7
    assert [item["id"] for item in data["items"]] == list(range(7))
    assert data["has_more"] is True
    # next_cursor 指向未取完的位置（出错的偏移 7），可凭此续取。
    assert decode_cursor(data["next_cursor"]) == 7


def test_partial_cursor_can_resume_after_recovery() -> None:
    client = TestClient(app)
    fetcher = _FailingFetcher(fail_at=7)

    with use_record_fetcher(fetcher):
        first = client.post(
            ENDPOINT, json={"source": "fixture", "limit": 10}
        )
        assert first.status_code == 200
        assert first.json()["status"] == "partial"
        resume_cursor = first.json()["next_cursor"]

        # 数据源仍未恢复：重试应拿到 failed，但绝不能是 500。
        retry = client.post(
            ENDPOINT,
            json={"source": "fixture", "cursor": resume_cursor, "limit": 10},
        )
        assert retry.status_code == 200
        retry_data = retry.json()
        assert retry_data["status"] == "failed"
        assert retry_data["items"] == []
        assert retry_data["fetched_count"] == 0
        assert retry_data["has_more"] is True
        # 一条没取到时 next_cursor 保留在原位置，便于继续重试。
        assert decode_cursor(retry_data["next_cursor"]) == 7

    # 数据源恢复后，用 partial 返回的游标从断点继续。
    def healthy_fetcher(source: str, offset: int) -> dict[str, Any] | None:
        return _record(offset, recovered=True) if offset < 20 else None

    with use_record_fetcher(healthy_fetcher):
        resumed = client.post(
            ENDPOINT,
            json={"source": "fixture", "cursor": resume_cursor, "limit": 5},
        )
    assert resumed.status_code == 200
    resumed_data = resumed.json()
    assert resumed_data["status"] == "ok"
    assert resumed_data["fetched_count"] == 5
    assert [item["id"] for item in resumed_data["items"]] == [7, 8, 9, 10, 11]
    assert decode_cursor(resumed_data["next_cursor"]) == 12


def test_error_on_first_record_returns_failed_not_500() -> None:
    client = TestClient(app)
    with use_record_fetcher(_FailingFetcher(fail_at=0)):
        response = client.post(
            ENDPOINT, json={"source": "fixture", "limit": 5}
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["fetched_count"] == 0
    assert data["items"] == []
    assert data["has_more"] is True
    assert decode_cursor(data["next_cursor"]) == 0


def test_probe_error_after_full_page_returns_partial() -> None:
    # 先正常给满 limit 条，探测第 limit 条时抛错。
    call_count = {"n": 0}

    def flaky_fetcher(source: str, offset: int) -> dict[str, Any] | None:
        call_count["n"] += 1
        if offset >= 3:
            raise RuntimeError("探测下一页时故障")
        return _record(offset)

    client = TestClient(app)
    with use_record_fetcher(flaky_fetcher):
        response = client.post(
            ENDPOINT, json={"source": "fixture", "limit": 3}
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial"
    assert data["fetched_count"] == 3
    assert decode_cursor(data["next_cursor"]) == 3
    assert data["has_more"] is True


# ---------------------------------------------------------------------------
# 参数校验：400
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "limit",
    [0, -1, 101, 1000, 1.5, "10", True, False, None],
)
def test_invalid_limit_returns_400(limit: Any) -> None:
    client = TestClient(app)
    response = client.post(
        ENDPOINT, json={"source": "demo_customers", "limit": limit}
    )
    assert response.status_code == 400
    assert "limit" in response.json()["detail"]


@pytest.mark.parametrize(
    "source",
    [None, "", "   ", 123, ["x"], {"k": "v"}],
)
def test_invalid_source_returns_400(source: Any) -> None:
    client = TestClient(app)
    response = client.post(ENDPOINT, json={"source": source, "limit": 10})
    assert response.status_code == 400
    assert "source" in response.json()["detail"]


def test_missing_fields_return_400() -> None:
    client = TestClient(app)
    assert client.post(ENDPOINT, json={}).status_code == 400
    assert (
        client.post(ENDPOINT, json={"source": "demo_customers"}).status_code
        == 400
    )
    assert (
        client.post(ENDPOINT, json={"limit": 10}).status_code == 400
    )


def test_garbled_cursor_returns_400() -> None:
    client = TestClient(app)
    for bad_cursor in ["not-a-cursor", "!!!", "v1:abc", "", None]:
        # None 是合法的“从头取”，其余都应 400。
        if bad_cursor is None:
            continue
        response = client.post(
            ENDPOINT,
            json={
                "source": "demo_customers",
                "limit": 10,
                "cursor": bad_cursor,
            },
        )
        assert response.status_code == 400
        assert "cursor" in response.json()["detail"]


def test_cursor_with_negative_offset_returns_400() -> None:
    # encode_cursor 不允许负数，直接用底层编码格式拼一个内容合法、
    # 但偏移为负的游标，验证解码侧的防御。
    import base64

    raw = base64.urlsafe_b64encode(b"v1:-3").decode("ascii").rstrip("=")
    client = TestClient(app)
    response = client.post(
        ENDPOINT,
        json={"source": "demo_customers", "limit": 10, "cursor": raw},
    )
    assert response.status_code == 400
    assert "cursor" in response.json()["detail"]


def test_cursor_out_of_bounds_returns_400() -> None:
    client = TestClient(app)
    # demo_orders 共 13 条，指向偏移 13 的游标之后没有记录。
    past_end = encode_cursor(13)
    response = client.post(
        ENDPOINT,
        json={"source": "demo_orders", "limit": 10, "cursor": past_end},
    )
    assert response.status_code == 400
    assert "cursor" in response.json()["detail"]


def test_cursor_for_unknown_source_is_treated_as_out_of_bounds() -> None:
    client = TestClient(app)
    response = client.post(
        ENDPOINT,
        json={
            "source": "missing",
            "limit": 10,
            "cursor": encode_cursor(2),
        },
    )
    assert response.status_code == 400


def test_cursor_roundtrip_encoding() -> None:
    for offset in [0, 1, 7, 100, 9999]:
        assert decode_cursor(encode_cursor(offset)) == offset
    with pytest.raises(CursorError):
        decode_cursor("%%%%")


# ---------------------------------------------------------------------------
# 并发隔离
# ---------------------------------------------------------------------------
def test_concurrent_requests_keep_independent_cursors_and_counts() -> None:
    total = len(SAMPLE_RECORDS["demo_customers"])  # 25
    worker_count = 8
    barrier = threading.Barrier(worker_count)

    def walk_full_dataset(worker_id: int) -> tuple[int, list[int], list[str]]:
        # 每个线程使用独立的 TestClient，避免客户端侧 cookie/状态共享。
        client = TestClient(app)
        barrier.wait()
        cursor: str | None = None
        collected: list[int] = []
        statuses: list[str] = []
        while True:
            body: dict[str, Any] = {
                "source": "demo_customers",
                # 不同 worker 用不同的 limit，制造交错的分页节奏。
                "limit": 3 + worker_id % 4,
            }
            if cursor is not None:
                body["cursor"] = cursor
            response = client.post(ENDPOINT, json=body)
            assert response.status_code == 200
            data = response.json()
            statuses.append(data["status"])
            collected.extend(item["id"] for item in data["items"])
            if not data["has_more"]:
                assert data["next_cursor"] is None
                break
            cursor = data["next_cursor"]
        return len(collected), collected, statuses

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(pool.map(walk_full_dataset, range(worker_count)))

    for count, ids, statuses in results:
        assert count == total
        assert ids == list(range(total))
        assert all(status == "ok" for status in statuses)


def test_concurrent_partial_and_ok_requests_do_not_cross_contaminate() -> None:
    # 两个 source：一个正常，一个在偏移 4 处故障，并发交错请求。
    healthy = SAMPLE_RECORDS["demo_orders"]

    def mixed_fetcher(source: str, offset: int) -> dict[str, Any] | None:
        if source == "broken":
            if offset >= 4:
                raise RuntimeError("broken source failure")
            return _record(offset, source=source)
        if source == "healthy":
            return healthy[offset] if offset < len(healthy) else None
        return None

    barrier = threading.Barrier(2)

    def walk_broken() -> dict[str, Any]:
        client = TestClient(app)
        barrier.wait()
        pages = []
        cursor: str | None = None
        for _ in range(10):
            body: dict[str, Any] = {
                "source": "broken",
                "limit": 3,
            }
            if cursor is not None:
                body["cursor"] = cursor
            data = client.post(ENDPOINT, json=body).json()
            pages.append((data["status"], data["fetched_count"]))
            cursor = data["next_cursor"]
            if data["status"] == "partial":
                break
        final = client.post(
            ENDPOINT, json={"source": "broken", "limit": 3, "cursor": cursor}
        ).json()
        return {"pages": pages, "retry": final}

    def walk_healthy() -> list[dict[str, Any]]:
        client = TestClient(app)
        barrier.wait()
        cursor = None
        items: list[dict[str, Any]] = []
        while True:
            body: dict[str, Any] = {"source": "healthy", "limit": 4}
            if cursor is not None:
                body["cursor"] = cursor
            data = client.post(ENDPOINT, json=body).json()
            assert data["status"] == "ok"
            items.extend(data["items"])
            if data["next_cursor"] is None:
                return items
            cursor = data["next_cursor"]

    with use_record_fetcher(mixed_fetcher):
        with ThreadPoolExecutor(max_workers=2) as pool:
            broken_future = pool.submit(walk_broken)
            healthy_future = pool.submit(walk_healthy)
            broken_result = broken_future.result()
            healthy_items = healthy_future.result()

    # broken source：先取到偏移 0..3，下一批在偏移 4 处 partial，重试 failed。
    assert broken_result["pages"] == [("ok", 3), ("partial", 1)]
    retry = broken_result["retry"]
    assert retry["status"] == "failed"
    assert retry["fetched_count"] == 0

    # healthy source 不受影响，完整取到 13 条且数据未被污染。
    assert len(healthy_items) == len(healthy)
    assert healthy_items == list(healthy)
