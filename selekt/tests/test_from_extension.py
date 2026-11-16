"""test_from_extension.py — 采集后端 from-extension 验收

执行（在 selekt/shopee-backend 目录，或设 PYTHONPATH）：
    python -m pytest ../tests/test_from_extension.py -q

覆盖：
  1. 任务市场优先：页面误报 market 不覆盖任务市场
  2. 缺价商品 price 存 NULL，不写成 0
  3. 无卡片（error + 空 products）→ 任务 failed，而非成功 0 件
  4. 部分成功（有商品 + error）→ done
  5. 手动同步（无 task_id）采用请求 market
  6. 未知市场回退 tw
  7. 接口价格 /100000 还原规则未变
"""
# database / main / scraper 位于 ../shopee-backend，运行时由 sys.path 注入，
# pylint 静态分析看不到，故关闭 import-error。
# 测试函数名即用例说明，pytest fixture 与测试参数同名是惯例。
# pylint: disable=import-error,wrong-import-position,redefined-outer-name
# pylint: disable=missing-function-docstring,unused-argument,import-outside-toplevel
import asyncio
import os
import sys
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "shopee-backend"
sys.path.insert(0, str(BACKEND))


def _stub_playwright():
    """本机 playwright 可能装不完整；接口测试用不到浏览器，��桩。"""
    if "playwright" in sys.modules:
        return
    pw = types.ModuleType("playwright")
    async_api = types.ModuleType("playwright.async_api")

    class _TimeoutError(Exception):
        pass

    async def _async_playwright():  # pragma: no cover - 测试中不会被调用
        raise RuntimeError("playwright 不用于本测试")

    async_api.async_playwright = _async_playwright
    async_api.TimeoutError = _TimeoutError
    pw.async_api = async_api
    sys.modules["playwright"] = pw
    sys.modules["playwright.async_api"] = async_api


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _stub_playwright()
    os.environ.pop("SELEKT_LICENSE_SECRET", None)

    import database as db
    import main

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "shopee.db")
    asyncio.run(db.init_db())

    # 阻止真实调度器启动
    main.scheduler.start = lambda *a, **k: None
    main.scheduler.shutdown = lambda *a, **k: None

    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c, db


def _products(db, job_id):
    async def _q():
        async with db.aiosqlite.connect(db.DB_PATH) as conn:
            conn.row_factory = db.aiosqlite.Row
            cur = await conn.execute("SELECT * FROM products WHERE job_id=?", (job_id,))
            return [dict(r) for r in await cur.fetchall()]
    return asyncio.run(_q())


def _new_job(db, keyword, market):
    job = asyncio.run(db.create_ext_job(keyword, market=market))
    asyncio.run(db.claim_ext_task(job))
    return job


def test_task_market_wins_over_page_guess(client):
    c, db = client
    job = _new_job(db, "sepatu", "id")
    r = c.post("/api/collect/from-extension", json={
        "task_id": job, "keyword": "sepatu", "market": "tw",
        "products": [{"name": "Sepatu", "price": 125000, "sales": 1200,
                      "market": "tw"}],
    })
    assert r.status_code == 200
    assert r.json()["market"] == "id"

    rows = _products(db, job)
    assert len(rows) == 1
    assert rows[0]["market"] == "id"
    assert asyncio.run(db.get_job(job))["status"] == "done"


def test_missing_price_stays_null(client):
    c, db = client
    job = _new_job(db, "no price", "id")
    c.post("/api/collect/from-extension", json={
        "task_id": job, "keyword": "no price", "market": "id",
        "products": [{"name": "Tanpa Harga", "price": None, "sales": 5,
                      "notes": ["⚠ 價格缺失，未計入價格評分"], "missing": ["price"]}],
    })
    rows = _products(db, job)
    assert rows[0]["price"] is None      # 不是 0
    assert rows[0]["sales"] == 5
    assert "價格缺失" in rows[0]["notes"]


def test_no_cards_marks_job_failed(client):
    c, db = client
    job = _new_job(db, "no cards", "th")
    r = c.post("/api/collect/from-extension", json={
        "task_id": job, "keyword": "no cards", "market": "th",
        "products": [], "error": "no_cards_found",
    })
    assert r.json()["status"] == "failed"
    assert asyncio.run(db.get_job(job))["status"] == "failed"


def test_partial_success_is_done(client):
    c, db = client
    job = _new_job(db, "partial", "vn")
    c.post("/api/collect/from-extension", json={
        "task_id": job, "keyword": "partial", "market": "vn",
        "error": "scroll_timeout",
        "products": [{"name": "Giày", "price": 120000, "sales": 2100}],
    })
    assert asyncio.run(db.get_job(job))["status"] == "done"


def test_manual_sync_uses_request_market(client):
    c, db = client
    r = c.post("/api/collect/from-extension", json={
        "keyword": "manual", "market": "my",
        "products": [{"name": "Beg", "price": 45.9, "sales": 1500}],
    })
    body = r.json()
    assert body["market"] == "my"
    assert _products(db, body["job_id"])[0]["market"] == "my"


def test_unknown_market_falls_back(client):
    c, _db = client
    r = c.post("/api/collect/from-extension", json={
        "keyword": "bogus", "market": "zz",
        "products": [{"name": "x", "price": 10}],
    })
    assert r.json()["market"] == "tw"


def test_api_price_scale_unchanged(client):
    _stub_playwright()
    import scraper
    parsed = scraper.parse_item(
        {"item_basic": {"name": "Test", "price": 12500000,
                        "shopid": 1, "itemid": 2}}, "id")
    assert parsed["price"] == 125          # 12500000 / 100000
