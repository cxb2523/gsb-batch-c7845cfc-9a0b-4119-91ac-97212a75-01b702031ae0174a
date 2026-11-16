"""
刮削链路缓存分源验收测试。

使用自造本地缓存目录（tmp_path）与假适配器验证：
1. JAVDB 失败降级 JAV321 后，JAVDB 缓存条目仍为空、JAV321 条目存在；
2. JAVDB 恢复后同一番号重新走网络并更新 JAVDB 缓存，不再复用降级数据；
3. JAVDB 缓存 TTL 内命中时零网络请求；强制刷新绕过缓存并回写对应来源；
4. 多线程并发写同一番号缓存，最终文件可正常解析且来源标识正确。
"""
import json
import threading

import pytest

from lib import detail_cache
from gui import scrape_worker
from gui.scrape_worker import ScrapeWorker


class FakeAdapter:
    """假适配器：可配置返回结果或抛异常，并记录调用次数。"""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def get_video_by_code(self, code):
        self.calls.append(code)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def cache_dir(monkeypatch, tmp_path):
    d = tmp_path / "detail_cache"
    monkeypatch.setattr(detail_cache, "CACHE_DIR", d)
    return d


@pytest.fixture
def adapters(monkeypatch):
    """替换 worker 的适配器工厂，返回 {platform_name: FakeAdapter}。"""
    registry = {}

    def fake_get_thread_adapter(platform_name, proxies=None):
        return registry[platform_name]

    monkeypatch.setattr(scrape_worker, "_get_thread_adapter", fake_get_thread_adapter)
    return registry


def run_worker(code, force_refresh=False):
    """同步执行一个 only_scrape 的虚拟任务 worker，返回 (preview_detail, status)。"""
    previews = []
    finishes = []
    worker = ScrapeWorker(f"__virtual__:{code}", code, "", "javdb",
                          only_scrape=True, force_refresh=force_refresh)
    worker.signals.preview_loaded.connect(lambda fp, d: previews.append(d))
    worker.signals.finished.connect(lambda fp, s: finishes.append(s))
    worker.run()
    assert finishes == ["scrape_success"]
    assert len(previews) == 1
    return previews[0]


CODE = "ABC-123"


# ---------- 缓存分源 ----------

def test_cache_keys_are_per_source(cache_dir):
    detail_cache.put("jav321", CODE, {"title": "降级数据"})
    # 降级源写入不影响主源条目
    assert detail_cache.get("javdb", CODE) is None
    degraded = detail_cache.get("jav321", CODE)
    assert degraded["title"] == "降级数据"
    assert degraded["source"] == "jav321"
    # 两个来源各有独立缓存文件
    names = [p.name for p in cache_dir.iterdir()]
    assert any("jav321" in n for n in names)
    assert not any("javdb_" in n for n in names)


# ---------- 降级与恢复 ----------

def test_fallback_writes_jav321_cache_only(cache_dir, adapters):
    adapters["javdb"] = FakeAdapter(error=ConnectionError("javdb down"))
    adapters["jav321"] = FakeAdapter(result={"title": "来自JAV321", "code": CODE})

    detail = run_worker(CODE)

    assert detail["source"] == "jav321"
    assert detail["title"] == "来自JAV321"
    # JAVDB 缓存条目仍为空，JAV321 条目存在
    assert detail_cache.get("javdb", CODE) is None
    assert detail_cache.get("jav321", CODE)["source"] == "jav321"


def test_javdb_recovery_overrides_degraded_cache(cache_dir, adapters):
    # 第一阶段：JAVDB 失败，留下 JAV321 降级缓存
    adapters["javdb"] = FakeAdapter(error=ConnectionError("javdb down"))
    adapters["jav321"] = FakeAdapter(result={"title": "降级数据", "code": CODE})
    run_worker(CODE)
    assert detail_cache.get("jav321", CODE) is not None

    # 第二阶段：JAVDB 恢复，同一番号应重新走网络并以 JAVDB 结果为准
    javdb_ok = FakeAdapter(result={"title": "JAVDB正主数据", "code": CODE})
    jav321_spy = FakeAdapter(result={"title": "不应被使用", "code": CODE})
    adapters["javdb"] = javdb_ok
    adapters["jav321"] = jav321_spy

    detail = run_worker(CODE)

    assert javdb_ok.calls == [CODE]          # 重新走了网络
    assert jav321_spy.calls == []            # 主源成功，无需降级
    assert detail["source"] == "javdb"
    assert detail["title"] == "JAVDB正主数据"
    # JAVDB 缓存已更新为主源结果
    assert detail_cache.get("javdb", CODE)["title"] == "JAVDB正主数据"


def test_javdb_cache_hit_avoids_network(cache_dir, adapters):
    javdb = FakeAdapter(result={"title": "正主", "code": CODE})
    adapters["javdb"] = javdb
    adapters["jav321"] = FakeAdapter(result={"title": "降级", "code": CODE})
    run_worker(CODE)
    assert javdb.calls == [CODE]

    # 第二次：JAVDB 缓存 TTL 内命中，两个适配器都不应再被调用
    detail = run_worker(CODE)
    assert javdb.calls == [CODE]
    assert adapters["jav321"].calls == []
    assert detail["source"] == "javdb"
    assert detail["title"] == "正主"


def test_degraded_cache_reused_while_javdb_down(cache_dir, adapters):
    # JAVDB 持续失败：第二次应复用 JAV321 降级缓存，不再重复打 JAV321
    adapters["javdb"] = FakeAdapter(error=ConnectionError("javdb down"))
    jav321 = FakeAdapter(result={"title": "降级数据", "code": CODE})
    adapters["jav321"] = jav321
    run_worker(CODE)
    assert jav321.calls == [CODE]

    detail = run_worker(CODE)
    assert jav321.calls == [CODE]            # 未再次出网
    assert detail["source"] == "jav321"
    assert detail["title"] == "降级数据"


def test_force_refresh_bypasses_cache(cache_dir, adapters):
    javdb_v1 = FakeAdapter(result={"title": "旧数据", "code": CODE})
    adapters["javdb"] = javdb_v1
    adapters["jav321"] = FakeAdapter()
    run_worker(CODE)
    assert detail_cache.get("javdb", CODE)["title"] == "旧数据"

    # 强制刷新：跳过缓存重新走网络，并回写 JAVDB 缓存
    javdb_v2 = FakeAdapter(result={"title": "新数据", "code": CODE})
    adapters["javdb"] = javdb_v2
    detail = run_worker(CODE, force_refresh=True)

    assert javdb_v2.calls == [CODE]
    assert detail["source"] == "javdb"
    assert detail["title"] == "新数据"
    assert detail_cache.get("javdb", CODE)["title"] == "新数据"


# ---------- 并发写缓存 ----------

def test_concurrent_puts_same_code(cache_dir):
    workers = 8
    rounds = 50
    errors = []

    def write_and_read(i):
        try:
            for r in range(rounds):
                detail_cache.put("jav321", CODE, {"title": f"t{i}-{r}", "i": i})
                loaded = detail_cache.get("jav321", CODE)
                if loaded is not None:
                    # 读到的必须是完整可解析的一整份，而非半写文件
                    assert loaded["source"] == "jav321"
                    assert loaded["i"] == int(loaded["title"].split("-")[0][1:])
        except Exception as e:  # pragma: no cover - 失败时记录
            errors.append(e)

    threads = [threading.Thread(target=write_and_read, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    final = detail_cache.get("jav321", CODE)
    assert final is not None
    assert final["source"] == "jav321"
    assert final["i"] == int(final["title"].split("-")[0][1:])
    # 不残留临时文件
    leftovers = [p.name for p in cache_dir.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_concurrent_puts_per_source_isolated(cache_dir):
    """多线程同时写同一番号的不同来源，最终两个条目各自完整。"""
    def write(source, tag):
        for r in range(30):
            detail_cache.put(source, CODE, {"title": f"{tag}-{r}"})

    threads = [threading.Thread(target=write, args=("javdb", "db")),
               threading.Thread(target=write, args=("jav321", "321"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    db = detail_cache.get("javdb", CODE)
    degraded = detail_cache.get("jav321", CODE)
    assert db["source"] == "javdb" and db["title"].startswith("db-")
    assert degraded["source"] == "jav321" and degraded["title"].startswith("321-")
