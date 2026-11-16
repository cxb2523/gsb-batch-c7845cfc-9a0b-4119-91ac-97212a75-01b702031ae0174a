from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError

from src.job_manager.playwright_manager import (
    PageFetchResult,
    PlaywrightJobManager,
    UnappliedFilter,
)

SECRETS = {
    "hh_login": "test@example.com",
    "hh_password": "secret",
    "tg_token": "token",
    "tg_api_id": "api_id",
    "tg_api_hash": "api_hash",
    "tg_chat_id": "chat_id",
    "tg_captcha_topic_id": "topic_id",
}


@pytest.fixture
def manager():
    return PlaywrightJobManager(SECRETS)


@pytest.fixture
def manager_with_page(manager):
    manager.page = MagicMock()
    manager.browser = MagicMock()
    manager.context = MagicMock()
    return manager


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_init(manager):
    assert manager.login == "test@example.com"
    assert manager.password == "secret"
    assert manager.browser is None
    assert manager.context is None
    assert manager.page is None
    assert manager.search_page_url == ""


# ---------------------------------------------------------------------------
# Статические вспомогательные методы
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, []),
        ("", []),
        ("Python, Django, Flask", ["Python", "Django", "Flask"]),
        ("Python; Django; Flask", ["Python", "Django", "Flask"]),
        ("  Python ,  Django  ", ["Python", "Django"]),
        (["Python", "Django"], ["Python", "Django"]),
        (["Python", "", "Django"], ["Python", "Django"]),
        (42, ["42"]),
        (0, []),
    ],
)
def test_split_multi(value, expected):
    assert PlaywrightJobManager._split_multi(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ({"a": True, "b": False, "c": True}, ["a", "c"]),
        ({"a": False}, []),
        ({}, []),
        ("not a dict", []),
        (None, []),
        (42, []),
    ],
)
def test_true_keys(value, expected):
    assert PlaywrightJobManager._true_keys(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ({"a": True, "b": False}, "a"),
        ({"a": False, "b": True}, "b"),
        ({"a": False}, None),
        ({}, None),
        (None, None),
    ],
)
def test_first_true_key(value, expected):
    assert PlaywrightJobManager._first_true_key(value) == expected


# ---------------------------------------------------------------------------
# pause_async
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_async(manager):
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await manager.pause_async(0.1, 0.2)
        mock_sleep.assert_awaited_once()
        elapsed = mock_sleep.call_args[0][0]
        assert 0.1 <= elapsed <= 0.2


# ---------------------------------------------------------------------------
# initialize / close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_creates_browser(manager):
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    with patch(
        "src.job_manager.playwright_manager.create_playwright_browser",
        new_callable=AsyncMock,
        return_value=(mock_browser, mock_context, mock_page),
    ):
        await manager.initialize()

    assert manager.browser is mock_browser
    assert manager.context is mock_context
    assert manager.page is mock_page


@pytest.mark.asyncio
async def test_initialize_skips_if_browser_exists(manager_with_page):
    existing_browser = manager_with_page.browser

    with patch(
        "src.job_manager.playwright_manager.create_playwright_browser",
        new_callable=AsyncMock,
    ) as mock_create:
        await manager_with_page.initialize()
        mock_create.assert_not_awaited()

    assert manager_with_page.browser is existing_browser


@pytest.mark.asyncio
async def test_close_releases_resources(manager_with_page):
    mock_context_close = AsyncMock()
    mock_browser_close = AsyncMock()
    manager_with_page.context.close = mock_context_close
    manager_with_page.browser.close = mock_browser_close

    with patch(
        "src.job_manager.playwright_manager.save_browser_session",
        new_callable=AsyncMock,
    ):
        await manager_with_page.close()

    mock_context_close.assert_awaited_once()
    mock_browser_close.assert_awaited_once()
    assert manager_with_page.context is None
    assert manager_with_page.browser is None
    assert manager_with_page.page is None


@pytest.mark.asyncio
async def test_close_with_no_resources(manager):
    """close() без браузера/контекста не должен бросать исключений."""
    await manager.close()


# ---------------------------------------------------------------------------
# ensure_logged_in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_logged_in_already_logged_in(manager_with_page):
    with (
        patch.object(manager_with_page, "pause_async", new_callable=AsyncMock),
        patch.object(manager_with_page, "_is_logged_in", new_callable=AsyncMock, return_value=True),
        patch.object(manager_with_page, "_perform_login", new_callable=AsyncMock) as mock_login,
    ):
        result = await manager_with_page.ensure_logged_in()

    assert result is True
    mock_login.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_logged_in_calls_perform_login(manager_with_page):
    with (
        patch.object(manager_with_page, "pause_async", new_callable=AsyncMock),
        patch.object(
            manager_with_page, "_is_logged_in", new_callable=AsyncMock, return_value=False
        ),
        patch.object(
            manager_with_page, "_perform_login", new_callable=AsyncMock, return_value=True
        ) as mock_login,
    ):
        result = await manager_with_page.ensure_logged_in()

    assert result is True
    mock_login.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_logged_in_initializes_if_no_page(manager):
    assert manager.page is None

    with (
        patch.object(manager, "initialize", new_callable=AsyncMock) as mock_init,
        patch.object(manager, "pause_async", new_callable=AsyncMock),
        patch.object(manager, "_is_logged_in", new_callable=AsyncMock, return_value=True),
    ):
        await manager.ensure_logged_in()

    mock_init.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_vacancies_from_page
# ---------------------------------------------------------------------------


@pytest.fixture
def _make_vacancy_card():
    """Фабрика заглушек карточек вакансий."""

    def _make(title: str, href: str, employer_name: str, employer_href: str):
        card = MagicMock()

        title_el = MagicMock()
        title_el.count = AsyncMock(return_value=1)
        title_el.get_attribute = AsyncMock(return_value=href)

        emp_el = MagicMock()
        emp_el.count = AsyncMock(return_value=1)
        emp_el.get_attribute = AsyncMock(return_value=employer_href)

        def locator_side_effect(selector):
            mock = MagicMock()
            if "serp-item__title" in selector:
                mock.first = title_el
            elif "vacancy-serp__vacancy-employer" in selector:
                mock.first = emp_el
            return mock

        card.locator = locator_side_effect

        return card, title, employer_name

    return _make


@pytest.mark.asyncio
async def test_get_vacancies_from_page_returns_list(manager_with_page, _make_vacancy_card):
    card, title, employer = _make_vacancy_card(
        "Python Developer",
        "https://hh.ru/vacancy/123456",
        "Test Corp",
        "https://hh.ru/employer/654321",
    )

    manager_with_page.page.url = "https://hh.ru/search/vacancy?page=0"
    manager_with_page.search_page_url = "https://hh.ru/search/vacancy?page=0"

    cards_locator = MagicMock()
    cards_locator.all = AsyncMock(return_value=[card])
    manager_with_page.page.locator = MagicMock(return_value=cards_locator)

    with (
        patch.object(manager_with_page, "pause_async", new_callable=AsyncMock),
        patch(
            "src.job_manager.playwright_manager.get_clean_text",
            new_callable=AsyncMock,
            side_effect=[title, employer],
        ),
    ):
        vacancies = await manager_with_page.get_vacancies_from_page(page_num=0)

    assert len(vacancies) == 1
    assert vacancies[0]["name"] == "Python Developer"
    assert vacancies[0]["id"] == "123456"
    assert vacancies[0]["alternate_url"] == "https://hh.ru/vacancy/123456"
    assert vacancies[0]["employer"]["id"] == "654321"
    assert vacancies[0]["employer"]["name"] == "Test Corp"


@pytest.mark.asyncio
async def test_get_vacancies_from_page_navigates_on_page_mismatch(manager_with_page):
    manager_with_page.search_page_url = "https://hh.ru/search/vacancy?page=0"
    manager_with_page.page.goto = AsyncMock()

    cards_locator = MagicMock()
    cards_locator.all = AsyncMock(return_value=[])
    manager_with_page.page.locator = MagicMock(return_value=cards_locator)

    with patch.object(manager_with_page, "pause_async", new_callable=AsyncMock):
        await manager_with_page.get_vacancies_from_page(page_num=2)

    manager_with_page.page.goto.assert_awaited_once()
    called_url = manager_with_page.page.goto.call_args[0][0]
    assert "page=2" in called_url


# ---------------------------------------------------------------------------
# _parse_vacancy_card
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_vacancy_card_success(manager_with_page):
    card = MagicMock()

    title_el = MagicMock()
    title_el.count = AsyncMock(return_value=1)
    title_el.get_attribute = AsyncMock(return_value="https://hh.ru/vacancy/111222")

    emp_el = MagicMock()
    emp_el.count = AsyncMock(return_value=1)
    emp_el.get_attribute = AsyncMock(return_value="https://hh.ru/employer/999")

    def _locator(selector):
        mock = MagicMock()
        if "serp-item__title" in selector:
            mock.first = title_el
        elif "vacancy-serp__vacancy-employer" in selector:
            mock.first = emp_el
        return mock

    card.locator = _locator

    with patch(
        "src.job_manager.playwright_manager.get_clean_text",
        new_callable=AsyncMock,
        side_effect=["Senior Python Developer", "Acme Inc"],
    ):
        result = await manager_with_page._parse_vacancy_card(card)

    assert result is not None
    assert result["name"] == "Senior Python Developer"
    assert result["id"] == "111222"
    assert result["alternate_url"] == "https://hh.ru/vacancy/111222"
    assert result["employer"]["id"] == "999"
    assert result["employer"]["name"] == "Acme Inc"


@pytest.mark.asyncio
async def test_parse_vacancy_card_no_title_returns_none(manager_with_page):
    card = MagicMock()

    title_el = MagicMock()
    title_el.count = AsyncMock(return_value=0)

    locator_mock = MagicMock()
    locator_mock.first = title_el
    card.locator = MagicMock(return_value=locator_mock)

    result = await manager_with_page._parse_vacancy_card(card)

    assert result is None


@pytest.mark.asyncio
async def test_parse_vacancy_card_no_href_returns_none(manager_with_page):
    card = MagicMock()

    title_el = MagicMock()
    title_el.count = AsyncMock(return_value=1)
    title_el.get_attribute = AsyncMock(return_value=None)

    locator_mock = MagicMock()
    locator_mock.first = title_el
    card.locator = MagicMock(return_value=locator_mock)

    with patch(
        "src.job_manager.playwright_manager.get_clean_text",
        new_callable=AsyncMock,
        return_value="Some Title",
    ):
        result = await manager_with_page._parse_vacancy_card(card)

    assert result is None


@pytest.mark.asyncio
async def test_parse_vacancy_card_relative_href(manager_with_page):
    """Относительный href должен дополняться до полного URL."""
    card = MagicMock()

    title_el = MagicMock()
    title_el.count = AsyncMock(return_value=1)
    title_el.get_attribute = AsyncMock(return_value="/vacancy/777888")

    emp_el = MagicMock()
    emp_el.count = AsyncMock(return_value=0)

    def _locator(selector):
        mock = MagicMock()
        if "serp-item__title" in selector:
            mock.first = title_el
        else:
            mock.first = emp_el
        return mock

    card.locator = _locator

    with patch(
        "src.job_manager.playwright_manager.get_clean_text",
        new_callable=AsyncMock,
        return_value="Dev",
    ):
        result = await manager_with_page._parse_vacancy_card(card)

    assert result is not None
    assert result["alternate_url"] == "https://hh.ru/vacancy/777888"


# ---------------------------------------------------------------------------
# _is_logged_in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_logged_in_true_when_menu_present(manager_with_page):
    manager_with_page.page.goto = AsyncMock()

    resume_menu = MagicMock()
    resume_menu.count = AsyncMock(return_value=1)
    resume_menu.or_.return_value.first.wait_for = AsyncMock()

    create_resume = MagicMock()
    create_resume.count = AsyncMock(return_value=0)

    def _locator(selector):
        if "profileAndResumes" in selector:
            return resume_menu
        return create_resume

    manager_with_page.page.locator = _locator

    result = await manager_with_page._is_logged_in()
    assert result is True


@pytest.mark.asyncio
async def test_is_logged_in_false_when_no_menu(manager_with_page):
    manager_with_page.page.goto = AsyncMock()

    no_element = MagicMock()
    no_element.count = AsyncMock(return_value=0)
    manager_with_page.page.locator = MagicMock(return_value=no_element)

    result = await manager_with_page._is_logged_in()
    assert result is False


# ---------------------------------------------------------------------------
# _handle_interfering_messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_interfering_messages_no_popups(manager_with_page):
    """Без попапов — цикл завершается без кликов."""
    no_element = MagicMock()
    no_element.count = AsyncMock(return_value=0)
    manager_with_page.page.locator = MagicMock(return_value=no_element)

    with patch.object(manager_with_page, "pause_async", new_callable=AsyncMock):
        await manager_with_page._handle_interfering_messages()

    manager_with_page.page.locator.assert_called()


@pytest.mark.asyncio
async def test_handle_interfering_messages_closes_cookies(manager_with_page):
    cookies_btn = MagicMock()
    cookies_btn.count = AsyncMock(return_value=1)
    cookies_btn.click = AsyncMock()

    no_element = MagicMock()
    no_element.count = AsyncMock(return_value=0)

    def _locator(selector):
        if "cookies-policy-informer-accept" in selector:
            return cookies_btn
        return no_element

    manager_with_page.page.locator = _locator

    with patch.object(manager_with_page, "pause_async", new_callable=AsyncMock):
        await manager_with_page._handle_interfering_messages()

    cookies_btn.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_interfering_messages_closes_data_collector_before_cookies(
    manager_with_page,
):
    """Опрос после отклика перекрывает страницу, поэтому закрывается раньше куки."""
    popup_close = MagicMock()
    popup_close.count = AsyncMock(return_value=1)
    popup_close.first.click = AsyncMock()

    cookies_btn = MagicMock()
    cookies_btn.count = AsyncMock(return_value=1)
    cookies_btn.click = AsyncMock()

    no_element = MagicMock()
    no_element.count = AsyncMock(return_value=0)

    def _locator(selector):
        if "additional-data-collector__popup-close" in selector:
            return popup_close
        if "cookies-policy-informer-accept" in selector:
            return cookies_btn
        return no_element

    manager_with_page.page.locator = _locator

    with patch.object(manager_with_page, "pause_async", new_callable=AsyncMock):
        await manager_with_page._handle_interfering_messages()

    popup_close.first.click.assert_awaited_once()
    cookies_btn.click.assert_not_awaited()


# ---------------------------------------------------------------------------
# _build_search_query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_search_query_maps_config_to_url_params(manager):
    """Параметры конфига превращаются в параметры URL поиска hh.ru, названия — в ID."""

    async def _dictionary(client, path, params=None):
        if path == "/suggests/areas":
            return [{"id": "1", "text": "Москва"}, {"id": "2095", "text": "Троицк (Москва)"}]
        if path == "/suggests/professional_roles":
            return [{"id": "96", "text": "Программист, разработчик"}]
        return []

    manager.search_params = {
        "job_title": "Python",
        "keywords": "Python разработчик",
        "search_field": {"name": True, "description": False},
        "words_to_exclude": "senior",
        "area": "Москва",
        "professional_role": "Программист, разработчик",
        "salary": 200000,
        "currency": {"RUR": True},
        "only_with_salary": True,
        "education": {"higher": True},
        "experience": {"doesntMatter": True},
        "employment": {"FULL": True, "INTERNSHIP": True, "ACCEPT_TEMPORARY": True},
        "job_format": {"REMOTE": True},
        "vacancy_label": {"not_from_agency": True},
        "order_by": {"publication_time": True},
        "period": {"week": True},
        "show": {"show_50": True},
    }

    with patch.object(PlaywrightJobManager, "_get_hh_dictionary", side_effect=_dictionary):
        query = await manager._build_search_query()

    assert query == [
        ("text", "Python разработчик"),
        ("search_field", "name"),
        ("excluded_text", "senior"),
        ("area", "1"),
        ("professional_role", "96"),
        ("salary", "200000"),
        ("currency", "RUR"),
        ("only_with_salary", "true"),
        ("education", "higher"),
        ("experience", "doesNotMatter"),
        ("employment_form", "FULL"),
        ("label", "internship"),
        ("accept_temporary", "true"),
        ("work_format", "REMOTE"),
        ("label", "not_from_agency"),
        ("order_by", "publication_time"),
        ("search_period", "7"),
        ("items_on_page", "50"),
    ]


# ---------------------------------------------------------------------------
# Неприменённые фильтры (сбой/неполнота справочников hh.ru)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dictionary_failure_recorded_as_unapplied_filter(manager):
    """Сбой справочника не выбрасывает фильтр молча: он попадает в сводку с причиной."""
    manager.search_params = {
        "area": "Москва, Питер",
        "professional_role": "Программист",
        "industry": "IT",
        "districts": "Центральный",
    }

    async def _failing_dictionary(client, path, params=None):
        raise httpx.ConnectError("connection refused")

    with patch.object(
        PlaywrightJobManager, "_fetch_hh_dictionary", side_effect=_failing_dictionary
    ):
        query = await manager._build_search_query()

    # Ни одного ID-фильтра в URL не попало
    assert not any(key in ("area", "professional_role", "industry", "district") for key, _ in query)

    unapplied = manager.get_unapplied_filters()
    assert len(unapplied) == 5  # 2 города + роль + отрасль + район
    by_type = {}
    for item in unapplied:
        assert item.reason == "dictionary_error"
        assert "connection refused" in item.detail
        by_type.setdefault(item.filter_type, []).append(item.requested_value)
    assert by_type["area"] == ["Москва", "Питер"]
    assert by_type["professional_role"] == ["Программист"]
    assert by_type["industry"] == ["IT"]
    assert by_type["district"] == ["Центральный"]

    # Сводка доступна отдельным методом и сериализуема для логов/уведомлений
    report = manager.get_search_run_report()
    dumped = report.to_dict()
    assert len(dumped["unapplied_filters"]) == 5
    assert dumped["unapplied_filters"][0]["filter_type"] == "area"


@pytest.mark.asyncio
async def test_unmatched_name_recorded_as_unapplied_filter(manager):
    """Название, которого нет в справочнике, фиксируется как no_match, остальные фильтры живут."""

    async def _dictionary(client, path, params=None):
        if path == "/suggests/areas":
            # Москва есть в справочнике, а по Несущеграду подсказок нет вовсе
            if params and params.get("text") == "Москва":
                return [{"id": "1", "text": "Москва"}]
            return []
        if path == "/suggests/professional_roles":
            return []  # справочник ответил, но вариантов нет
        if path == "/industries":
            return []  # отраслей в ответе справочника нет
        return []

    manager.search_params = {
        "area": "Москва, Несущеград",
        "professional_role": "Космонавт",
        "industry": "Рыболовство",
    }

    with patch.object(PlaywrightJobManager, "_fetch_hh_dictionary", side_effect=_dictionary):
        query = await manager._build_search_query()

    # Москва по-прежнему превратилась в ID
    assert ("area", "1") in query
    assert not any(key == "industry" for key, _ in query)
    # Несущеград, роль и отрасль отброшены не молча
    unapplied = manager.get_unapplied_filters()
    no_match = {(f.filter_type, f.requested_value): f for f in unapplied}
    assert no_match[("area", "Несущеград")].reason == "no_match"
    assert no_match[("professional_role", "Космонавт")].reason == "no_match"
    assert no_match[("industry", "Рыболовство")].reason == "no_match"


@pytest.mark.asyncio
async def test_set_advanced_search_params_resets_report(manager_with_page):
    """Каждый новый запуск поиска начинает сводку с чистого листа."""
    manager_with_page.search_report.unapplied_filters.append(
        UnappliedFilter(filter_type="area", requested_value="old", reason="no_match")
    )
    manager_with_page.page.goto = AsyncMock()
    manager_with_page.page.url = "https://hh.ru/search/vacancy"

    async def _dictionary(client, path, params=None):
        return [{"id": "1", "text": "Москва"}]

    with (
        patch.object(PlaywrightJobManager, "_fetch_hh_dictionary", side_effect=_dictionary),
        patch.object(manager_with_page, "pause_async", new_callable=AsyncMock),
        patch.object(manager_with_page, "_handle_interfering_messages", new_callable=AsyncMock),
    ):
        await manager_with_page.set_advanced_search_params({"area": "Москва"})

    assert manager_with_page.get_unapplied_filters() == []
    assert "area=1" in manager_with_page.search_page_url


@pytest.mark.asyncio
async def test_district_matching_filters_other_city_options(manager):
    """Районы сопоставляются только среди справочника найденных городов."""

    async def _dictionary(client, path, params=None):
        if path == "/suggests/areas":
            return [{"id": "1", "text": "Москва"}]
        if path == "/districts":
            return [
                {"id": "11", "name": "Центральный", "area_id": "1"},
                {"id": "22", "name": "Северный", "area_id": "2"},
            ]
        return []

    manager.search_params = {"area": "Москва", "districts": "Центральный"}

    with patch.object(PlaywrightJobManager, "_fetch_hh_dictionary", side_effect=_dictionary):
        query = await manager._build_search_query()

    # Район чужого города (area_id=2) в опции не попал, Центральный сопоставился
    assert ("district", "11") in query
    assert manager.get_unapplied_filters() == []


@pytest.mark.asyncio
async def test_district_no_match_when_city_not_resolved(manager):
    """Без найденного города у районов нет вариантов — район фиксируется как no_match."""

    async def _dictionary(client, path, params=None):
        if path == "/suggests/areas":
            return []  # город не найден -> area_ids пуст
        if path == "/districts":
            return [{"id": "22", "name": "Северный", "area_id": "2"}]
        return []

    manager.search_params = {"area": "Несущеград", "districts": "Центральный"}

    with patch.object(PlaywrightJobManager, "_fetch_hh_dictionary", side_effect=_dictionary):
        query = await manager._build_search_query()

    assert not any(key == "district" for key, _ in query)
    unapplied = {(f.filter_type, f.requested_value): f for f in manager.get_unapplied_filters()}
    assert unapplied[("area", "Несущеград")].reason == "no_match"
    assert unapplied[("district", "Центральный")].reason == "no_match"


@pytest.mark.asyncio
async def test_invalid_json_body_recorded_as_dictionary_error(manager):
    """Нечитаемое тело ответа справочника не роняет сбор, а помечается как сбой фильтра."""
    manager.search_params = {"area": "Москва"}

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="это не JSON", headers={"content-type": "text/plain"})

    transport = httpx.MockTransport(_handler)
    real_async_client = httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    with patch("src.job_manager.playwright_manager.httpx.AsyncClient", side_effect=_client_factory):
        query = await manager._build_search_query()

    assert not any(key == "area" for key, _ in query)
    [item] = manager.get_unapplied_filters()
    assert item.filter_type == "area"
    assert item.reason == "dictionary_error"
    assert "JSON" in item.detail


@pytest.mark.asyncio
async def test_legacy_get_hh_dictionary_swallows_errors(manager_with_page):
    """Устаревший помощник по-прежнему возвращает [] вместо исключения."""

    async def _failing_dictionary(client, path, params=None):
        raise httpx.ConnectError("offline")

    with patch.object(
        PlaywrightJobManager, "_fetch_hh_dictionary", side_effect=_failing_dictionary
    ):
        result = await PlaywrightJobManager._get_hh_dictionary(
            MagicMock(), "/suggests/areas", {"text": "М"}
        )

    assert result == []


# ---------------------------------------------------------------------------
# fetch_vacancies_page: постраничная статистика и изоляция ошибок
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_page_keeps_parsed_cards_when_one_card_fails(
    manager_with_page, _make_vacancy_card
):
    """Исключение на одной карточке не теряет уже разобранные кандидаты."""
    good_card, title, employer = _make_vacancy_card(
        "Python Developer",
        "https://hh.ru/vacancy/123456",
        "Test Corp",
        "https://hh.ru/employer/654321",
    )
    broken_card = MagicMock()
    broken_card.locator.side_effect = PlaywrightError("card detached")

    manager_with_page.search_page_url = "https://hh.ru/search/vacancy?page=0"
    cards_locator = MagicMock()
    cards_locator.all = AsyncMock(return_value=[good_card, broken_card])
    manager_with_page.page.locator = MagicMock(return_value=cards_locator)

    with patch(
        "src.job_manager.playwright_manager.get_clean_text",
        new_callable=AsyncMock,
        side_effect=[title, employer],
    ):
        result = await manager_with_page.fetch_vacancies_page(page_num=0)

    assert result.ok is True
    assert result.success_count == 1
    assert result.cards_count == 2
    assert len(result.failed_cards) == 1
    assert result.failed_cards[0]["card_index"] == 1
    assert "card detached" in result.failed_cards[0]["error"]
    # Структура кандидата не изменилась
    assert set(result.vacancies[0].keys()) == {"name", "id", "alternate_url", "employer"}
    assert result.vacancies[0]["name"] == "Python Developer"


@pytest.mark.asyncio
async def test_fetch_page_navigation_failure_returns_error_result(manager_with_page):
    """Не открывшаяся страница отличается от пустой выдачи: error заполнен, сбор продолжается."""
    manager_with_page.search_page_url = "https://hh.ru/search/vacancy?page=0"
    manager_with_page.page.goto = AsyncMock(side_effect=PlaywrightError("net::ERR_FAILED"))

    with patch.object(manager_with_page, "pause_async", new_callable=AsyncMock):
        result = await manager_with_page.fetch_vacancies_page(page_num=3)

    assert result.ok is False
    assert result.vacancies == []
    assert result.success_count == 0
    assert "3" in result.error and "ERR_FAILED" in result.error

    report = manager_with_page.get_search_run_report()
    assert report.failed_pages == [result]
    assert isinstance(result, PageFetchResult)


@pytest.mark.asyncio
async def test_fetch_page_results_accumulate_in_report(manager_with_page, _make_vacancy_card):
    """Сводка хранит успех каждой страницы: номер, число карт, число успехов."""
    card, title, employer = _make_vacancy_card(
        "Dev",
        "https://hh.ru/vacancy/1",
        "Corp",
        "https://hh.ru/employer/2",
    )
    manager_with_page.search_page_url = "https://hh.ru/search/vacancy?page=0"
    cards_locator = MagicMock()
    cards_locator.all = AsyncMock(return_value=[card])
    manager_with_page.page.locator = MagicMock(return_value=cards_locator)

    with patch(
        "src.job_manager.playwright_manager.get_clean_text",
        new_callable=AsyncMock,
        side_effect=[title, employer],
    ):
        first = await manager_with_page.fetch_vacancies_page(page_num=0)
    assert first.success_count == 1

    # Повторный сбор той же страницы перезаписывает результат, а не дублирует
    cards_locator.all = AsyncMock(return_value=[])
    second = await manager_with_page.fetch_vacancies_page(page_num=0)

    report = manager_with_page.get_search_run_report()
    assert len(report.pages) == 1
    assert report.pages[0] is second
    assert report.pages[0].cards_count == 0


@pytest.mark.asyncio
async def test_get_vacancies_from_page_still_returns_plain_list(
    manager_with_page, _make_vacancy_card
):
    """Существующий контракт get_vacancies_from_page не нарушен: возвращается список карточек."""
    card, title, employer = _make_vacancy_card(
        "Python Developer",
        "https://hh.ru/vacancy/123456",
        "Test Corp",
        "https://hh.ru/employer/654321",
    )
    manager_with_page.search_page_url = "https://hh.ru/search/vacancy?page=0"
    cards_locator = MagicMock()
    cards_locator.all = AsyncMock(return_value=[card])
    manager_with_page.page.locator = MagicMock(return_value=cards_locator)

    with patch(
        "src.job_manager.playwright_manager.get_clean_text",
        new_callable=AsyncMock,
        side_effect=[title, employer],
    ):
        vacancies = await manager_with_page.get_vacancies_from_page(page_num=0)

    assert isinstance(vacancies, list)
    assert len(vacancies) == 1
    assert set(vacancies[0].keys()) == {"name", "id", "alternate_url", "employer"}


@pytest.mark.asyncio
async def test_fetch_page_locator_failure_returns_error_result(manager_with_page):
    """Падение чтения списка карточек — провал страницы, а не пустая выдача."""
    manager_with_page.search_page_url = "https://hh.ru/search/vacancy?page=0"
    broken_locator = MagicMock()
    broken_locator.all = AsyncMock(side_effect=PlaywrightError("frame detached"))
    manager_with_page.page.locator = MagicMock(return_value=broken_locator)

    result = await manager_with_page.fetch_vacancies_page(page_num=0)

    assert result.ok is False
    assert "frame detached" in result.error
    assert result.vacancies == []
    assert manager_with_page.get_search_run_report().failed_pages == [result]


@pytest.mark.asyncio
async def test_fetch_page_without_hh_url_skips_navigation(manager_with_page):
    """Если search_page_url пуст, навигация не выполняется: используется текущий page.url."""
    manager_with_page.search_page_url = ""
    manager_with_page.page.url = "https://example.local/listing"
    manager_with_page.page.goto = AsyncMock()
    cards_locator = MagicMock()
    cards_locator.all = AsyncMock(return_value=[])
    manager_with_page.page.locator = MagicMock(return_value=cards_locator)

    result = await manager_with_page.fetch_vacancies_page(page_num=2)

    manager_with_page.page.goto.assert_not_awaited()
    assert result.ok is True
    assert result.cards_count == 0


# ---------------------------------------------------------------------------
# get_vacancy_full_info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_vacancy_full_info_retries_aborted_navigation(manager_with_page):
    """Разово прерванный переход на страницу вакансии повторяется."""
    manager_with_page.page.goto = AsyncMock(side_effect=[PlaywrightError("net::ERR_ABORTED"), None])
    manager_with_page.page.url = "https://hh.ru/vacancy/123"
    no_element = MagicMock()
    no_element.count = AsyncMock(return_value=0)
    manager_with_page.page.locator = MagicMock(return_value=no_element)

    with patch.object(manager_with_page, "pause_async", new_callable=AsyncMock):
        info = await manager_with_page.get_vacancy_full_info("https://hh.ru/vacancy/123")

    assert manager_with_page.page.goto.await_count == 2
    assert info["vacancy_id"] == "123"


@pytest.mark.asyncio
async def test_get_vacancy_full_info_raises_after_second_failure(manager_with_page):
    """Если и повторный переход не удался, ошибка пробрасывается."""
    manager_with_page.page.goto = AsyncMock(side_effect=PlaywrightError("net::ERR_ABORTED"))

    with (
        patch.object(manager_with_page, "pause_async", new_callable=AsyncMock),
        pytest.raises(PlaywrightError),
    ):
        await manager_with_page.get_vacancy_full_info("https://hh.ru/vacancy/123")

    assert manager_with_page.page.goto.await_count == 2
