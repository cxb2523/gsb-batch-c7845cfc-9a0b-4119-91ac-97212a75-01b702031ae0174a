#!/usr/bin/env python3
"""Офлайн-проверка продолжения обхода с места прерывания (браузер не нужен).

Гоняет Discoverer по фальшивым страницам выдачи против настоящего SQLite:
  1. обрыв после 3-й страницы -> повторный запуск продолжает с 4-й;
  2. страница продолжения исчезла (выдача сократилась) -> источник завершается
     штатно, точка продолжения снимается;
  3. итоговые статусы (done/failed) сбрасывают точку продолжения, pending - нет.

Запуск: python tools/test_resume.py
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from venues2gis.config import CollectConfig  # noqa: E402
from venues2gis.discover import Discoverer  # noqa: E402
from venues2gis.store import Store  # noqa: E402

BASE_URL = "https://2gis.test/almaty/search/кафе"


class FakePage:
    """Выдача 2GIS в миниатюре: {номер страницы: [firm_id, ...]}.

    Переход по URL на несуществующую страницу сбрасывает на первую - так же
    ведёт себя настоящий 2GIS (см. CLAUDE.md, "Грабли").
    """

    def __init__(self, pages: dict[int, list[str]], total: int | None = None) -> None:
        self.pages = pages
        self.total = total
        self.current = 1
        self.goto_urls: list[str] = []
        self.read_pages: list[int] = []  # страницы, у которых спрашивали firm_id

    @property
    def url(self) -> str:
        return BASE_URL if self.current == 1 else f"{BASE_URL}/page/{self.current}"

    def goto(self, url: str, referer: str | None = None, timeout: int | None = None) -> None:
        self.goto_urls.append(url)
        m = re.search(r"/page/(\d+)", url)
        n = int(m.group(1)) if m else 1
        self.current = n if n in self.pages else 1

    def evaluate(self, script: str, arg=None):
        if 'a[href*="/firm/"]' in script:
            self.read_pages.append(self.current)
            return [f"/firm/{fid}" for fid in self.pages.get(self.current, [])]
        if '"total"' in script:
            return [self.total] if self.total else []
        if arg is not None and "el.click()" in script:
            if arg in self.pages:
                self.current = arg
                return True
            return False
        if arg is not None and ".some(" in script:
            return arg in self.pages
        raise AssertionError(f"неожиданный evaluate: {script[:80]}")


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    def wait_network_idle(self, *_args) -> None:
        pass

    def is_antibot_page(self) -> bool:
        return False


def make_cfg() -> CollectConfig:
    # Нулевые задержки, чтобы тест не спал; лимиты как в боевом конфиге.
    return CollectConfig(page_delay_ms=(0, 0), click_delay_ms=(0, 0))


def run_one_task(store: Store, session: FakeSession, cfg: CollectConfig,
                 should_stop=lambda: False):
    """Мини-копия цикла воркера: атомарно взять задачу, выполнить, записать итог."""
    task = store.next_task(cfg.max_attempts)
    assert task is not None, "очередь задач пуста, а должна была отдать задачу"
    disc = Discoverer(session, store, cfg, should_stop=should_stop)
    res = disc.run_task(task["region"], task["domain"], task["query"],
                        task_id=task["id"],
                        resume_page=task["resume_page"] or 0,
                        resume_seen=task["resume_seen"] or 0)
    store.finish_task(task["id"], res.status, res.pages_done, res.ids_seen,
                      res.ids_new, res.error, res.reported_total)
    return task["id"], res


def task_row(store: Store, task_id: int) -> dict:
    row = store._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return dict(row)


def firms_count(store: Store) -> int:
    return store._conn.execute("SELECT COUNT(*) FROM firms").fetchone()[0]


def test_interrupt_and_resume() -> None:
    print("== сценарий 1: обрыв после 3-й страницы, продолжение с 4-й ==")
    tmp = Path(tempfile.mkdtemp(prefix="resume1_"))
    pages = {n: [str(n * 100 + i) for i in range(3)] for n in range(1, 6)}  # 5 стр. по 3 id
    cfg = make_cfg()

    with Store(tmp / "state.sqlite", tmp / "raw") as store:
        store.enqueue_tasks([("almaty", "2gis.test", "кафе")])

        # --- первый заход: "умираем" после третьей прочитанной страницы ---
        page = FakePage(pages, total=15)
        session = FakeSession(page)
        should_stop = lambda: len(page.read_pages) >= 3  # noqa: E731
        task_id, res = run_one_task(store, session, cfg, should_stop)
        assert res.status == "pending", res
        assert res.pages_done == 3 and res.ids_seen == 9, res
        row = task_row(store, task_id)
        assert row["status"] == "pending", row
        assert row["resume_page"] == 3 and row["resume_seen"] == 9, row
        assert firms_count(store) == 9
        print(f"  прервано: страниц {res.pages_done}, просмотрено {res.ids_seen}, "
              f"точка продолжения = стр. {row['resume_page']}")

        # --- второй заход: "перезапуск", должны начать сразу со страницы 4 ---
        page2 = FakePage(pages, total=15)
        session2 = FakeSession(page2)
        task_id2, res2 = run_one_task(store, session2, cfg)
        assert task_id2 == task_id
        assert page2.goto_urls[0].endswith("/page/4"), page2.goto_urls
        assert page2.read_pages == [4, 5], page2.read_pages
        assert res2.status == "done", res2
        # Накопительные счётчики: 9 до прерывания + 6 в этом заходе.
        assert res2.pages_done == 5 and res2.ids_seen == 15, res2
        assert res2.ids_new == 6, res2  # страницы 1-3 не переучтены
        assert firms_count(store) == 15  # дедуп: повторного заезда по стр. 1-3 не было
        row = task_row(store, task_id)
        assert row["status"] == "done", row
        assert row["resume_page"] is None and row["resume_seen"] == 0, row
        assert row["cards_seen"] == 15, row
        print(f"  продолжено со стр. 4: итого страниц {res2.pages_done}, "
              f"просмотрено {res2.ids_seen}, новых {res2.ids_new}, точка снята")
    print("  OK")


def test_missing_resume_page() -> None:
    print("== сценарий 2: страница продолжения исчезла (выдача сократилась) ==")
    tmp = Path(tempfile.mkdtemp(prefix="resume2_"))
    pages = {n: [str(n * 100 + i) for i in range(3)] for n in range(1, 4)}  # всего 3 стр.
    cfg = make_cfg()

    with Store(tmp / "state.sqlite", tmp / "raw") as store:
        store.enqueue_tasks([("almaty", "2gis.test", "кафе")])
        # Имитируем прошлый обрыв: задача была прервана после 5-й страницы,
        # но с тех пор выдача сократилась до трёх.
        task = store.next_task(cfg.max_attempts)
        store.save_resume(task["id"], 5, 50)
        store.finish_task(task["id"], "pending", 5, 50, 0, "остановлено")

        page = FakePage(pages, total=50)
        session = FakeSession(page)
        task_id, res = run_one_task(store, session, cfg)
        # Прямой заход на /page/6 сброшен на стр. 1, докликаться до 6-й нельзя -
        # источник штатно завершается, исключения нет.
        assert res.status == "done", res
        assert res.ids_seen == 50 and res.pages_done == 5, res
        row = task_row(store, task_id)
        assert row["status"] == "done", row
        assert row["resume_page"] is None and row["resume_seen"] == 0, row
        print(f"  исчезнувшая стр. 6 обработана как конец списка: статус {res.status}, "
              "точка продолжения снята")
    print("  OK")


def test_resume_cleared_on_terminal_status() -> None:
    print("== сценарий 3: точку продолжения хранит только прерванная задача ==")
    tmp = Path(tempfile.mkdtemp(prefix="resume3_"))
    with Store(tmp / "state.sqlite", tmp / "raw") as store:
        store.enqueue_tasks([("almaty", "2gis.test", "q1"),
                             ("almaty", "2gis.test", "q2"),
                             ("almaty", "2gis.test", "q3")])
        t1 = store.next_task()
        store.save_resume(t1["id"], 4, 40)
        store.finish_task(t1["id"], "failed", error="антибот")
        assert task_row(store, t1["id"])["resume_page"] is None

        t2 = store.next_task()
        store.save_resume(t2["id"], 2, 20)
        store.finish_task(t2["id"], "empty")
        assert task_row(store, t2["id"])["resume_page"] is None

        t3 = store.next_task()
        store.save_resume(t3["id"], 7, 70)
        store.finish_task(t3["id"], "pending", 7, 70, 0, "остановлено")
        row = task_row(store, t3["id"])
        assert row["resume_page"] == 7 and row["resume_seen"] == 70, row
        print("  failed/empty сбрасывают точку, pending сохраняет")
    print("  OK")


if __name__ == "__main__":
    test_interrupt_and_resume()
    test_missing_resume_page()
    test_resume_cleared_on_terminal_status()
    print("\nВсе проверки продолжения обхода прошли.")
