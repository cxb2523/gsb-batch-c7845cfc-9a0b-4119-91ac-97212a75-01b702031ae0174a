# -*- coding: utf-8 -*-
"""XControlP — synchronisation (collect.py) on demand and at start. Moved unchanged from XControlP.pyw."""
import importlib.util
import threading
from .config import (COLLECTOR)


def run_sync():
    """Кнопка «Синхронизировать данные»: сборщик → auto.json → data.js. Возвращает итог для окна."""
    with SYNC_LOCK:
        before = effective_stages()
        old_auto = read_auto() or {}
        spec = importlib.util.spec_from_file_location("pult_collect", COLLECTOR)
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        auto = collector.collect(fetch=True)
        updates = check_in_background()
        calendar = calendar_sync_quiet()
        digest = digest_sync()
        data = build() or {}
        projects = {p.get("id"): p for p in data.get("projects", [])}
        old_events = {(e.get("date"), pid, e.get("text"))
                      for pid, r in (old_auto.get("projects") or {}).items() for e in r.get("events", [])}
        new_events = sum(1 for pid, r in auto["projects"].items() for e in r.get("events", [])
                         if (e.get("date"), pid, e.get("text")) not in old_events)
        lines = []
        for pid, result in auto["projects"].items():
            p = projects.get(pid, {})
            stages = p.get("stages") or []
            now, was = p.get("current", 0), before.get(pid, 0)
            line = {"id": pid, "name": result.get("name", pid), "ok": result.get("ok", False),
                    "status": result.get("status", "ok" if result.get("ok") else "failed"),
                    "local_only": bool(result.get("local_only")), "error": result.get("error", "")}
            if result.get("ok"):
                line.update(stage=stages[now] if stages else "", moved=now != was, was=stages[was] if now != was and stages else "")
            lines.append(line)
        commits = (auto.get("stats") or {}).get("commits") or {}
        stats_errors = commits.get("errors") if isinstance(commits.get("errors"), list) else []
        # Общий итог синхронизации: failed — ни один проект не прочитан; partial — часть проектов или
        # часть статистики не удалась, но остальное собрано; ok — всё прочитано (предупреждения — не ошибки).
        failed = [p for p in lines if not p["ok"]]
        if failed and len(failed) == len(lines):
            overall = "failed"
        elif failed or stats_errors:
            overall = "partial"
        else:
            overall = "ok"          # нет проектов с источником или всё прочитано — провалов нет
        return {"ok": True, "status": overall, "synced": auto.get("synced"), "seconds": auto.get("seconds"),
                "projects": lines, "counts": auto.get("counts", {}),
                "stats": {"errors": stats_errors, "repos": len(commits.get("repos", []))},
                "notes": auto.get("notes", []), "events": new_events, "calendar": calendar, "digest": digest,
                "updates_check": updates}


SYNC_GATE = threading.Lock()          # одна синхронизация за раз: по кнопке или при запуске
STARTUP_SYNC = {"started": False}
STARTUP_LOCK = threading.Lock()


def sync_request():
    """Кнопка: если синхронизация уже идёт (при запуске или из другого окна), вторая не начинается."""
    if not SYNC_GATE.acquire(blocking=False):
        return {"ok": False, "busy": True, "error": "Синхронизация уже идёт"}
    try:
        return run_sync()
    finally:
        SYNC_GATE.release()


def startup_sync_request():
    """Синхронизация при включении Пульта: один раз за запуск программы; перезагрузка окна её не повторяет."""
    with STARTUP_LOCK:
        if STARTUP_SYNC["started"]:
            return {"ok": True, "skipped": True}
        STARTUP_SYNC["started"] = True
    return sync_request()


# Neighbours are imported at the end: build() reads every area and the writers call build() again,
# so the modules refer to each other; by the time any function runs, these names exist.
from .calendar_glue import (calendar_sync_quiet)  # noqa: E402
from .build import (SYNC_LOCK, build, effective_stages, read_auto)  # noqa: E402
from .digests import (digest_sync)  # noqa: E402
from .updates import (check_in_background)  # noqa: E402
