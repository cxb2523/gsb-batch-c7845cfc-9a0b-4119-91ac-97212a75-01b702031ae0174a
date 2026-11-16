from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

from src.job_manager.playwright_manager import PlaywrightJobManager


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
