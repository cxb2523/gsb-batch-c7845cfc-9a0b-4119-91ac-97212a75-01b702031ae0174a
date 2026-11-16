"""
按番号磁盘缓存刮削详情，缓存键包含来源站点（javdb / jav321）。

设计要点：
- 不同来源分文件存放，降级源（jav321）命中时绝不覆盖主源（javdb）的条目；
- 读写在 detail 中盖 ``source`` 字段，界面与 NFO 可直接区分详情来自哪个站点；
- 写入采用"每线程唯一临时文件 + os.replace 原子替换"，多个 worker 并发
  写同一番号不会互相覆盖，读取方也永远不会看到半写文件；
- 同一番号在 TTL 内重复刮削直接命中本地缓存，零网络请求——
  既加速批量重跑，也降低出网频率与封禁风险。
"""
import itertools
import json
import os
import re
import threading
import time

import config

CACHE_DIR = config.DATA_DIR / "detail_cache"
TTL_SECONDS = 7 * 86400  # 7 天：元数据基本不变，磁力/评分允许一周内滞后
# 解析规则变化后隔离旧缓存，避免继续使用误取的推荐影片标题。
CACHE_VERSION = 2

# 来源标识：主源 javdb，降级源 jav321。缓存文件名与 detail["source"] 均使用。
SOURCE_JAVDB = "javdb"
SOURCE_JAV321 = "jav321"

# 临时文件序号：配合 pid + 线程 id 保证并发写同一缓存键时临时文件名唯一
_tmp_seq = itertools.count()


def _cache_path(source: str, code: str):
    safe = re.sub(r'[^A-Za-z0-9_-]', '_', f"{source}_{code.upper()}")
    return CACHE_DIR / f"v{CACHE_VERSION}_{safe}.json"


def get(source: str, code: str):
    """命中且未过期返回 detail dict（已盖 source 标识），否则返回 None。"""
    path = _cache_path(source, code)
    try:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > TTL_SECONDS:
            return None
        with open(path, "r", encoding="utf-8") as f:
            detail = json.load(f)
        if isinstance(detail, dict):
            # 以来源缓存键为准盖章，旧版本未写 source 的条目也能被正确识别
            detail["source"] = source
        return detail
    except Exception:
        return None


def put(source: str, code: str, detail: dict):
    """写入指定来源的缓存，失败静默（缓存不可用不应影响主流程）。"""
    if not detail:
        return
    tmp = None
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(source, code)
        # 浅拷贝后盖章：不污染调用方持有的 detail 对象
        payload = dict(detail)
        payload["source"] = source
        # 临时文件名带 pid/线程 id/序号：多 worker 并发写同一番号时
        # 各写各的临时文件，os.replace 原子落盘，读者不会看到半写文件
        tmp = "{}.{}.{}.{}.tmp".format(
            path, os.getpid(), threading.get_ident(), next(_tmp_seq))
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        tmp = None
    except Exception:
        pass
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass
