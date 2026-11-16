"""Сквозная проверка поисковой цепочки без обращения к реальному hh.ru.

Используются:
- httpx.MockTransport — реальный httpx-клиент с локальным «словарным» API;
- MagicMock вместо Playwright Page — навигация и карточки выдачи полностью подделаны.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError

from src.job_manager.playwright_manager import PlaywrightJobManager

SECRETS = {"hh_login": "test@example.com", "hh_password": "secret"}


def _make_card_mock(title, vacancy_href, employer_name, employer_href, *, broken=False):
    card = MagicMock()
    if broken:
        card.locator.side_effect = PlaywrightError("card detached from DOM")
        return card

    title_el = MagicMock()
    title_el.count = AsyncMock(return_value=1)
    title_el.get_attribute = AsyncMock(return_value=vacancy_href)

    emp_el = MagicMock()
    emp_el.count = AsyncMock(return_value=1)
    emp_el.get_attribute = AsyncMock(return_value=employer_href)

    def _locator(selector):
        mock = MagicMock()
        if "serp-item__title" in selector:
            mock.first = title_el
        elif "vacancy-serp__vacancy-employer" in selector:
            mock.first = emp_el
        return mock

    card.locator = _locator
    return card, title, employer_name


def _dictionary_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/suggests/areas":
        text = request.url.params.get("text", "")
        if text == "Москва":
            return httpx.Response(200, json={"items": [{"id": "1", "text": "Москва"}]})
        if text == "Сбойград":
            return httpx.Response(503, json={"error": "service unavailable"})
        return httpx.Response(200, json={"items": []})
    if path == "/suggests/professional_roles":
        # Роль не находится: справочник отвечает пустым списком
        return httpx.Response(200, json={"items": []})
    if path == "/industries":
        return httpx.Response(
            200, json=[{"id": "7", "name": "Информационные технологии", "industries": []}]
        )
    if path == "/districts":
        return httpx.Response(200, json=[])
    return httpx.Response(404, json={"error": "unknown endpoint"})


@pytest.mark.asyncio
async def test_full_search_collection_is_explainable_with_mocks():
    manager = PlaywrightJobManager(SECRETS)
    manager.page = MagicMock()
    manager.page.goto = AsyncMock()
    manager.page.url = "https://hh.ru/search/vacancy?page=0"

    # Реальный httpx.AsyncClient, но трафик перехватывается локальным обработчиком
    transport = httpx.MockTransport(_dictionary_handler)
    real_async_client = httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    manager.search_params = {
        "keywords": "Python",
        "area": "Москва, Сбойград, Несущеград",
        "professional_role": "Космонавт",
        "industry": "Информационные технологии",
    }

    with (
        patch("src.job_manager.playwright_manager.httpx.AsyncClient", side_effect=_client_factory),
        patch.object(manager, "pause_async", new_callable=AsyncMock),
        patch.object(manager, "_handle_interfering_messages", new_callable=AsyncMock),
    ):
        await manager.set_advanced_search_params(manager.search_params)

    # 1. Неприменённые фильтры различимы по причинам и доступны верхнему слою
    unapplied = manager.get_unapplied_filters()
    reasons = {(f.filter_type, f.requested_value): f.reason for f in unapplied}
    assert reasons[("area", "Сбойград")] == "dictionary_error"
    assert reasons[("area", "Несущеград")] == "no_match"
    assert reasons[("professional_role", "Космонавт")] == "no_match"
    # Применимые фильтры попали в URL, неприменимые — нет
    assert "area=1" in manager.search_page_url
    assert "industry=7" in manager.search_page_url
    assert "professional_role" not in manager.search_page_url

    # 2. Страница 0: одна битая карточка не теряет хорошую
    good_card, good_title, good_employer = _make_card_mock(
        "Python Developer",
        "https://hh.ru/vacancy/100",
        "Good Corp",
        "https://hh.ru/employer/200",
    )
    broken_card = _make_card_mock(None, None, None, None, broken=True)

    page0_locator = MagicMock()
    page0_locator.all = AsyncMock(return_value=[good_card, broken_card])

    # 3. Страница 1: навигация падает
    call_count = {"n": 0}
    original_goto = manager.page.goto

    async def _goto(url, *args, **kwargs):
        if "page=1" in url:
            raise PlaywrightError("net::ERR_ABORTED")
        return await original_goto(url, *args, **kwargs)

    manager.page.goto = AsyncMock(side_effect=_goto)

    def _locator_switch(selector):
        if selector == '[data-qa="vacancy-serp__vacancy"]':
            call_count["n"] += 1
            if call_count["n"] == 1:
                return page0_locator
            empty = MagicMock()
            empty.all = AsyncMock(return_value=[])
            return empty
        return MagicMock()

    manager.page.locator = MagicMock(side_effect=_locator_switch)

    with patch(
        "src.job_manager.playwright_manager.get_clean_text",
        new_callable=AsyncMock,
        side_effect=[good_title, good_employer],
    ):
        page0 = await manager.fetch_vacancies_page(0)
    page1 = await manager.fetch_vacancies_page(1)

    # Хорошая карточка сохранена в прежней структуре, битая — посчитана отдельно
    assert page0.success_count == 1
    assert page0.cards_count == 2
    assert len(page0.failed_cards) == 1
    assert page0.vacancies[0] == {
        "name": "Python Developer",
        "id": "100",
        "alternate_url": "https://hh.ru/vacancy/100",
        "employer": {"id": "200", "name": "Good Corp"},
    }

    # Провальная страница отличима от пустой: есть номер и причина
    assert page1.ok is False
    assert page1.page_num == 1
    assert "ERR_ABORTED" in page1.error
    assert page1.vacancies == []

    # 4. Итоговая сводка хранит и фильтры, и постраничную статистику
    report = manager.get_search_run_report()
    assert len(report.unapplied_filters) == 3
    assert [p.page_num for p in report.pages] == [0, 1]
    assert [p.success_count for p in report.pages] == [1, 0]
    assert [p.error for p in report.failed_pages] == [page1.error]
    # Словарный вид пригоден для логов/уведомлений без знания внутренних классов
    dumped = report.to_dict()
    assert dumped["pages"][0]["failed_cards"][0]["card_index"] == 1
    assert dumped["unapplied_filters"][0]["reason"] in {"dictionary_error", "no_match"}
