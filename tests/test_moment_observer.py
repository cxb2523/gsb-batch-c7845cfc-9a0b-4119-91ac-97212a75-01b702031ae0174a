# -*- coding: utf-8 -*-
"""观察闭环结算逻辑单测（不触微信、不开窗口、不需要真实数据库）。

运行方式（在本目录）::

    python -m unittest tests.test_moment_observer -v

做法：用临时目录伪造 ``<account>/cache/<月>/Sns/{Img,Video}/<2hex>/<30hex>``
缓存树 + 一个只带 ``account_dir`` 的假 db 构造 ``MomentDB``/``MomentObserver``；
缓存文件内容即明文（非 V2 容器时 ``decrypt_cache`` 原样返回字节）。
``MomentDB._export_root`` 被打到临时目录，避免在真实 ~/Documents 下落盘。
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from wechatauto.moment import MomentDB  # noqa: E402
from wechatauto.moment_observer import MomentObserver  # noqa: E402


class _FakeDB:
    """MomentDB 需要的最小 db 替身：只有 account_dir。"""

    def __init__(self, account_dir: str):
        self.account_dir = account_dir


def _key_str(seed: int) -> str:
    return "%032x" % seed


class MomentObserverCaptureTest(unittest.TestCase):
    """capture 结算：分类、轮次、结局、去重、失败不中断。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = self._tmp.name
        self.account_dir = os.path.join(root, "account")
        self.export_root = os.path.join(root, "export")
        self.mapping_path = os.path.join(self.export_root, "mapping.json")
        os.makedirs(self.account_dir, exist_ok=True)
        # 导出根也收敛到临时目录（export_cache_key 内部会自建 _export_root）
        patcher = mock.patch.object(
            MomentDB, "_export_root",
            staticmethod(lambda base=None: self.export_root))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.obs = MomentObserver(db=_FakeDB(self.account_dir),
                                  export_root=self.export_root,
                                  mapping_path=self.mapping_path)

    # ------------------------------------------------------------------
    # 缓存树构造 / 增删
    # ------------------------------------------------------------------
    def _cache_path(self, key_str: str, month: str = "2026-11",
                    kind: str = "Img") -> str:
        d = os.path.join(self.account_dir, "cache", month, "Sns",
                         kind, key_str[:2])
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, key_str[2:])

    def _add_cache(self, key_str: str, content: bytes,
                   month: str = "2026-11", kind: str = "Img") -> str:
        p = self._cache_path(key_str, month, kind)
        with open(p, "wb") as fp:
            fp.write(content)
        return p

    def _remove_cache(self, key_str: str, month: str = "2026-11",
                      kind: str = "Img") -> None:
        os.remove(self._cache_path(key_str, month, kind))

    def _capture(self, plan, steps=5, target=0):
        """plan: {0 基 step: callable()}，在该轮 diff 前执行（模拟滚动触发下载）。

        注意：capture 开始前已存在的缓存属于基线快照，不算「新增」，
        所以样例 key 一律通过 plan 在轮次内添加。
        """

        def action(step):
            fn = plan.get(step)
            if fn:
                fn()

        return self.obs.capture(steps=steps, target=target, action=action,
                                read_screen=False)

    # ------------------------------------------------------------------
    # 用例
    # ------------------------------------------------------------------
    def test_target_reached_and_round_attribution(self):
        """两轮各出 2 个 key，target=3 → 第二轮达标即停，条目带轮次。"""
        def step0():
            self._add_cache(_key_str(1), b"img-one")
            self._add_cache(_key_str(2), b"img-two")

        def step1():
            self._add_cache(_key_str(3), b"img-three")
            self._add_cache(_key_str(4), b"img-four")

        res = self._capture({0: step0, 1: step1}, steps=8, target=3)

        self.assertEqual(res["outcome"], MomentObserver.OUTCOME_TARGET_REACHED)
        # target 按轮检查：第二轮内 2 个 key 全部结算后才停，故 4 >= 3
        self.assertEqual(len(res["exported"]), 4)
        self.assertGreaterEqual(len(res["exported"]), 3)
        self.assertEqual(res["failed"], [])
        by_key = {r["key"][2]: r for r in res["exported"]}
        self.assertEqual(by_key[_key_str(1)]["round"], 1)
        self.assertEqual(by_key[_key_str(3)]["round"], 2)
        self.assertEqual(res["steps_run"], 2)  # 达标提前结束，没跑满 8 轮
        # 每轮汇总与明细一致
        self.assertEqual(sum(r["exported"] for r in res["rounds"]), 4)

    def test_failure_has_reason_and_does_not_interrupt(self):
        """一个 key 解密失败：带 reason、不中断同轮其余 key 与后续轮。"""
        def step0():
            self._add_cache(_key_str(1), b"good-1")
            self._add_cache(_key_str(2), b"bad")
            self._add_cache(_key_str(3), b"good-3")

        def step1():
            self._add_cache(_key_str(4), b"good-4")

        real_decrypt = self.obs.mdb.decrypt_cache

        def flaky(path):
            with open(path, "rb") as fp:
                if fp.read() == b"bad":
                    return None
            return real_decrypt(path)

        with mock.patch.object(self.obs.mdb, "decrypt_cache", flaky):
            res = self._capture({0: step0, 1: step1}, steps=3, target=0)

        self.assertEqual(res["outcome"], MomentObserver.OUTCOME_STEPS_EXHAUSTED)
        self.assertEqual(len(res["exported"]), 3)       # 好 key 全部固化
        self.assertEqual(len(res["failed"]), 1)
        fail = res["failed"][0]
        self.assertEqual(fail["key"][2], _key_str(2))
        self.assertEqual(fail["round"], 1)
        self.assertTrue(fail.get("reason"))             # 可判读原因
        # 失败不中断后续轮次：第 2 轮新增的 key 也导出了
        self.assertIn(_key_str(4),
                      {r["key"][2] for r in res["exported"]})

    def test_no_new_keys_outcome_is_not_failure(self):
        """全程无新增 → no_new_keys，空结果不与失败混淆。"""
        res = self._capture({}, steps=3, target=0)
        self.assertEqual(res["outcome"], MomentObserver.OUTCOME_NO_NEW_KEYS)
        self.assertEqual(res["exported"], [])
        self.assertEqual(res["skipped"], [])
        self.assertEqual(res["failed"], [])
        self.assertEqual(res["steps_run"], 3)

    def test_steps_exhausted_when_target_missed(self):
        """有导出但没到 target → steps_exhausted。"""
        res = self._capture({0: lambda: self._add_cache(_key_str(1), b"only-one")},
                            steps=2, target=5)
        self.assertEqual(res["outcome"], MomentObserver.OUTCOME_STEPS_EXHAUSTED)
        self.assertEqual(len(res["exported"]), 1)

    def test_same_key_across_rounds_settled_once(self):
        """缓存被清理后同 key 重新出现：不重复结算、不重复计入成功数。"""
        def step0():
            self._add_cache(_key_str(1), b"dup-content")

        def step1():
            self._remove_cache(_key_str(1))     # 微信清理缓存

        def step2():
            self._add_cache(_key_str(1), b"dup-content")  # 同 key 重新下载

        res = self._capture({0: step0, 1: step1, 2: step2}, steps=4, target=0)

        self.assertEqual(len(res["exported"]), 1)   # 只结算一次
        self.assertEqual(res["exported"][0]["round"], 1)
        # 后续轮次不再对该 key 产生任何结算记录
        all_keys = [r["key"] for r in
                    res["exported"] + res["skipped"] + res["failed"]]
        self.assertEqual(all_keys.count(res["exported"][0]["key"]), 1)

    def test_idempotent_skip_classified(self):
        """同 key 同内容已在映射里 → skip 归类，不计入成功数。"""
        first = self._capture(
            {0: lambda: self._add_cache(_key_str(1), b"already")},
            steps=1, target=0)
        self.assertEqual(len(first["exported"]), 1)

        # 第二次闭环：缓存文件还在（调用方以空基线重跑，同 key 同内容）
        res = self.obs.capture(steps=1, target=0, before={},
                               action=lambda s: None, read_screen=False)
        self.assertEqual(len(res["exported"]), 0)
        self.assertEqual(len(res["skipped"]), 1)
        self.assertEqual(res["skipped"][0]["round"], 1)
        self.assertEqual(res["outcome"],
                         MomentObserver.OUTCOME_STEPS_EXHAUSTED)

    def test_corrupt_mapping_fails_honestly(self):
        """映射文件损坏 → 导出报失败，不静默当成功、不覆盖既有映射。"""
        first = self._capture(
            {0: lambda: self._add_cache(_key_str(1), b"first")},
            steps=1, target=0)
        self.assertEqual(len(first["exported"]), 1)
        with open(self.mapping_path, "r", encoding="utf-8") as fp:
            good_mapping = fp.read()

        # 损坏映射后新增 key：必须进 failed 且带 reason
        with open(self.mapping_path, "w", encoding="utf-8") as fp:
            fp.write("{not valid json")

        def step0():
            self._add_cache(_key_str(2), b"second")

        res = self._capture({0: step0}, steps=1, target=0)
        self.assertEqual(len(res["exported"]), 0)
        self.assertEqual(len(res["failed"]), 1)
        self.assertIn("mapping", res["failed"][0]["reason"])
        # 既有映射未被静默清空重写
        with open(self.mapping_path, "r", encoding="utf-8") as fp:
            self.assertEqual(fp.read(), "{not valid json")
        self.assertNotEqual(good_mapping, "")

    def test_unwritable_mapping_fails_honestly(self):
        """映射路径不可写（被同名目录占位）→ 如实报失败。"""
        os.makedirs(self.export_root, exist_ok=True)
        os.makedirs(self.mapping_path, exist_ok=True)  # 目录占住映射路径
        res = self._capture(
            {0: lambda: self._add_cache(_key_str(1), b"content")},
            steps=1, target=0)
        self.assertEqual(len(res["exported"]), 0)
        self.assertEqual(len(res["failed"]), 1)
        self.assertTrue(res["failed"][0].get("reason"))

    def test_export_exception_isolated_per_key(self):
        """export 抛出未预期异常也只影响该 key，批次继续。"""
        self._add_cache(_key_str(1), b"a")
        self._add_cache(_key_str(2), b"b")
        new_keys = self.obs.diff({})

        real_export = self.obs.export
        calls = {"n": 0}

        def boom(key, path, **meta):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated crash")
            return real_export(key, path, **meta)

        with mock.patch.object(self.obs, "export", boom):
            results = self.obs.export_new(new_keys, round_no=1)

        self.assertEqual(len(results), 2)
        errors = [r for r in results if r["status"] == "error"]
        oks = [r for r in results if r["status"] == "ok"]
        self.assertEqual(len(errors), 1)
        self.assertIn("simulated crash", errors[0]["reason"])
        self.assertEqual(len(oks), 1)
        for r in results:
            self.assertEqual(r["round"], 1)
            self.assertIn("key", r)


if __name__ == "__main__":
    unittest.main()
