import asyncio
import os
import random
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from Levenshtein import distance
from playwright.async_api import Browser, BrowserContext
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.logger_config import logger
from src.telegram.telegram_manager import process_captcha
from src.utils.browser_utils import (
    create_playwright_browser,
    get_clean_text,
    safe_click,
    safe_fill,
    save_browser_session,
)
from src.utils.utils import sanitize_text
from src.views.resume import Resume

HH_SEARCH_URL = "https://hh.ru/search/vacancy"
HH_API_URL = "https://api.hh.ru"
HH_API_USER_AGENT = "XX-Auto-Jobs-Applier/1.0"


@dataclass
class UnappliedFilter:
    """Фильтр поиска, который не удалось применить к URL выдачи.

    `filter_type` — тип фильтра (area / professional_role / industry / district),
    `requested_value` — название, заданное пользователем в search_config,
    `reason` — код причины: "dictionary_error" (справочник hh.ru не ответил)
    или "no_match" (в справочнике не найдено подходящее название),
    `detail` — человекочитаемое пояснение (текст ошибки запроса и т.п.).
    """

    filter_type: str
    requested_value: str
    reason: str
    detail: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class PageFetchResult:
    """Итог сбора одной страницы выдачи.

    `vacancies` — успешно разобранные карточки; структура каждого элемента
    не меняется (name, id, alternate_url, employer).
    `error` заполнен, когда страницу не удалось открыть/прочитать целиком.
    """

    page_num: int
    vacancies: List[Dict[str, Any]] = field(default_factory=list)
    success_count: int = 0
    cards_count: int = 0
    failed_cards: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SearchRunReport:
    """Сводка по сбору поиска: неприменённые фильтры и постраничная статистика."""

    unapplied_filters: List[UnappliedFilter] = field(default_factory=list)
    pages: List[PageFetchResult] = field(default_factory=list)

    @property
    def failed_pages(self) -> List[PageFetchResult]:
        return [page for page in self.pages if not page.ok]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unapplied_filters": [f.to_dict() for f in self.unapplied_filters],
            "pages": [page.to_dict() for page in self.pages],
        }


class PlaywrightJobManager:
    """
    Управляет экземпляром браузера Playwright, аутентификацией и высокоуровневыми взаимодействиями.
    """

    def __init__(self, secrets: dict):
        self.secrets = secrets
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.login = secrets.get("hh_login")
        self.password = secrets.get("hh_password")
        self.search_page_url = ""
        # Сводка последнего поиска: неприменённые фильтры и статистика страниц
        self.search_report: SearchRunReport = SearchRunReport()

    async def initialize(self):
        """Инициализирует браузер, контекст и страницу."""
        if not self.browser:
            self.browser, self.context, self.page = await create_playwright_browser()

    async def close(self):
        """Закрывает ресурсы браузера."""
        if self.context:
            await save_browser_session(self.context)
            await self.context.close()
            self.context = None
        if self.browser:
            await self.browser.close()
            self.browser = None
        self.page = None

    async def ensure_logged_in(self) -> bool:
        """Проверяет авторизацию, если нет - выполняет вход."""
        if not self.page:
            await self.initialize()
        logger.info("Проверяем статус авторизации...")
        await self.pause_async(2, 3)
        if not await self._is_logged_in():
            return await self._perform_login()
        return True

    async def _perform_login(self) -> bool:
        """Выполняет процесс входа."""
        # Нажимаем кнопку входа
        if not await safe_click(self.page, "[data-qa*='login']"):
            logger.error("Кнопка входа не найдена")
            return False

        # В некоторых случаях сначала появляется выбор типа аккаунта (работодатель/соискатель).
        # Всегда выбираем соискателя ("Я ищу работу").
        await self.pause_async(1, 2)
        logger.info("Обрабатываем выбор типа аккаунта")
        await self._handle_account_type_chooser_if_present()

        # HH может по умолчанию использовать телефон; переключаемся на email, если переключатель есть.
        logger.info("Выбираем тип учётных данных: email")
        await self._select_email_credential_type_if_present()

        # Сначала вводим логин (email) — HH может требовать это перед переходом к форме пароля
        await self.pause_async(1, 2)
        logger.info("Вводим логин")
        await safe_fill(
            self.page,
            "//*[@data-qa='applicant-login-input-email']",
            self.login,
            wait_for_timeout=2000,
        )

        # Открываем форму пароля (кнопка "Войти с паролем")
        logger.info("Открываем форму пароля")
        await safe_click(self.page, "//*[starts-with(@data-qa, 'expand-login-by')]")
        await self.pause_async(1, 2)

        # Вводим пароль
        logger.info("Вводим пароль")
        await safe_fill(
            self.page,
            "//*[@data-qa='login-input-password' or @data-qa='applicant-login-input-password']",
            self.password,
            wait_for_timeout=10000,
        )
        await self.pause_async(2, 3)

        # Нажимаем кнопку "Войти" (не нажимаем "Дальше" раньше времени)
        await safe_click(self.page, "//*[@data-qa='submit-button']", timeout=10000)
        await self.pause_async(2, 3)

        # Проверяем наличие ошибок
        error_msg = self.page.locator("//*[@data-qa='account-login-error']")
        if await error_msg.count() > 0:
            text = await get_clean_text(error_msg.first)
            logger.error(f"Ошибка входа: {text}")
            return False

        # Проверяем успешность входа
        if await self._is_logged_in():
            logger.info("Вход выполнен успешно.")
            # Сохраняем сессию браузера после успешного входа
            await save_browser_session(self.context)
            return True
        else:
            logger.warning("Проверка входа не прошла.")
            return False

    async def _is_logged_in(self) -> bool:
        """Проверяет, выполнен ли вход."""
        logger.info("Переходим на страницу входа...")
        try:
            await self.page.goto("https://hh.ru/employer", wait_until="domcontentloaded")
            logger.info("Переход на страницу: https://hh.ru/employer")
        except Exception as e:
            logger.warning(f"Не удалось перейти на страницу входа: {e}")
            logger.info("Пробуем продолжить...")

        try:
            resume_menu = self.page.locator('[data-qa="mainmenu_profileAndResumes"]')
            create_resume_button = self.page.locator('[data-qa="mainmenu_createResume"]')
            # Страница открывается по domcontentloaded, ждём отрисовки меню
            try:
                await resume_menu.or_(create_resume_button).first.wait_for(timeout=10000)
            except PlaywrightTimeoutError:
                pass

            if await resume_menu.count() > 0 or await create_resume_button.count() > 0:
                logger.info("Пользователь уже авторизован.")
                return True
        except Exception as e:
            logger.warning(f"Ошибка при проверке статуса авторизации: {e}")
        return False

    async def _handle_account_type_chooser_if_present(self) -> None:
        """
        Обрабатывает выбор типа аккаунта (работодатель/соискатель), если он появляется.
        Если появляется, выбирает соискателя ("Я ищу работу") и нажимает "Войти".
        """
        if not self.page:
            return

        chooser_container = self.page.locator("//*[@data-qa='account-type-cards']")
        applicant_card = self.page.locator(
            "xpath=//*[contains(@data-qa, 'account-type-card-APPLICANT')]/ancestor::label[1]"
        )
        submit_btn = self.page.locator("//*[@data-qa='submit-button']")

        try:
            has_container = (await chooser_container.count()) > 0
            has_applicant = (await applicant_card.count()) > 0
            has_submit = (await submit_btn.count()) > 0
        except Exception as e:
            logger.warning(f"Ошибка при проверке выбора типа аккаунта: {e}")
            return

        if not (has_container or (has_applicant and has_submit)):
            return

        logger.info("Обнаружен выбор типа аккаунта. Выбираем аккаунт соискателя...")

        # Кликаем по карточке соискателя (по data-qa); откат на поиск по тексту.
        clicked = await safe_click(
            self.page,
            "//*[contains(@data-qa,'account-type-card-APPLICANT')]/ancestor::label[1]",
            timeout=10000,
        )
        if not clicked:
            await safe_click(
                self.page,
                "//*[.//span[@data-qa='cell-text-content' and contains(., 'Я') and contains(., 'ищу работу')]]",
                timeout=10000,
            )

        await safe_click(self.page, "//*[@data-qa='submit-button']", timeout=10000)
        await self.pause_async(1, 2)

    async def _select_email_credential_type_if_present(self) -> None:
        """
        Переключает тип входа на Email, если выбран телефон.
        Вход для соискателя HH может показывать переключатель типа учетных данных (ТЕЛЕФОН vs EMAIL).
        Если присутствует и выбран ТЕЛЕФОН, переключается на EMAIL ("Почта").
        """
        if not self.page:
            return

        switcher = self.page.locator("//*[@data-qa='credential-type-switch']")
        if (await switcher.count()) == 0:
            return

        # В разметке HH выбранное состояние может иметь data-qa="credential-type-PHONE checked"
        phone_checked = self.page.locator("[data-qa*='credential-type-PHONE'][data-qa*='checked']")
        if (await phone_checked.count()) == 0:
            return

        logger.info("Обнаружен переключатель типа учётных данных. Переключаемся на EMAIL...")
        clicked = await safe_click(
            self.page,
            "//*[@data-qa='credential-type-EMAIL']/ancestor::label[1]",
            timeout=10000,
        )
        if not clicked:
            await safe_click(
                self.page,
                "//*[self::label or self::div][.//*[contains(., 'Почта')]]",
                timeout=10000,
            )
        await self.pause_async(0.5, 1)

    async def _handle_captcha(self, submit_selector: str):
        """Обрабатывает капчу, если она появляется."""
        captcha_img = self.page.locator("//*[@data-qa='account-captcha-picture']")

        start_time = datetime.now()

        while await captcha_img.count() > 0:
            if (datetime.now() - start_time).total_seconds() > 3600:
                logger.error("Капча не решена за 1 час.")
                break

            logger.info("Обнаружена капча.")

            img_path = "captcha_image.png"
            message_id = str(int(datetime.now().timestamp() * 10**6))

            # Отправляем капчу (или всегда свежий скриншот)
            try:
                await captcha_img.first.screenshot(path=img_path)
            except Exception as e:
                logger.error(f"Не удалось сохранить изображение капчи: {e}")
                break

            tg_token = self.secrets["tg_token"]
            tg_api_id = self.secrets.get("tg_api_id")
            tg_api_hash = self.secrets.get("tg_api_hash")
            tg_chat_id = self.secrets["tg_chat_id"]
            tg_topic_id = self.secrets["tg_captcha_topic_id"]

            # Отправляем изображение
            await process_captcha(
                tg_token,
                tg_api_id,
                tg_api_hash,
                tg_chat_id,
                tg_topic_id,
                img_path,
                message_id,
                listen=False,
            )

            # Ждём ответа
            answer = await process_captcha(
                tg_token,
                tg_api_id,
                tg_api_hash,
                tg_chat_id,
                tg_topic_id,
                img_path,
                message_id,
                listen=True,
            )

            if answer:
                logger.info(f"Получен ответ на капчу: {answer}")
                await safe_fill(self.page, "//*[@data-qa='account-captcha-input']", answer)
                await safe_click(self.page, submit_selector)

                # Ждём перезагрузки/проверки
                await self.pause_async(5, 6)
                if os.path.exists(img_path):
                    os.remove(img_path)
            else:
                await self.pause_async(5, 6)

    async def pause_async(self, low=0.5, high=1.0):
        """Асинхронная пауза. Время паузы выбирается случайно в пределах между low и high секунд."""
        await asyncio.sleep(random.uniform(low, high))

    async def set_advanced_search_params(self, search_params: Dict[str, Any]) -> None:
        """
        Формирует URL поиска hh.ru из настроек `search_config.yaml` и открывает выдачу.

        `search_params` ожидается в "сыром" виде (как в YAML / `SearchConfig.model_dump()`).
        hh.ru убрал страницу расширенного поиска, поэтому фильтры передаются параметрами URL.
        """
        self.search_params = search_params or {}
        # Новый поиск — новая сводка; неприменённые фильтры заполняет _build_search_query
        self.search_report = SearchRunReport()
        query = await self._build_search_query()
        url = f"{HH_SEARCH_URL}?{urllib.parse.urlencode(query)}"
        await self.page.goto(url, wait_until="domcontentloaded")
        logger.info(f"Переход на страницу поиска: {url}")
        self.search_page_url = url
        unapplied = self.search_report.unapplied_filters
        if unapplied:
            logger.warning(
                "Поиск запущен с неполными условиями, фильтры не применены: "
                + "; ".join(
                    f"{item.filter_type}='{item.requested_value}' ({item.reason}: {item.detail})"
                    for item in unapplied
                )
            )
        await self.pause_async(2, 3)
        await self._handle_interfering_messages()

    def get_unapplied_filters(self) -> List[UnappliedFilter]:
        """Фильтры, которые не удалось превратить в ID при последнем построении запроса."""
        return self.search_report.unapplied_filters

    def get_search_run_report(self) -> SearchRunReport:
        """Сводка последнего поиска: неприменённые фильтры и постраничная статистика."""
        return self.search_report

    # -----------------------------
    # Вспомогательные методы расширенного поиска (UI)
    # -----------------------------

    @staticmethod
    def _split_multi(value: Any) -> List[str]:
        """Разделяет строку с несколькими значениями (через запятую или точку с запятой)."""
        if not value:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if not isinstance(value, str):
            return [str(value).strip()] if str(value).strip() else []
        # Принимаем ввод, разделённый запятыми или точками с запятой
        raw = value.replace(";", ",")
        return [v.strip() for v in raw.split(",") if v.strip()]

    @staticmethod
    def _true_keys(value: Any) -> List[str]:
        """Возвращает список ключей словаря, значения которых True."""
        if not isinstance(value, dict):
            return []
        return [k for k, v in value.items() if v is True]

    @staticmethod
    def _first_true_key(value: Any) -> Optional[str]:
        """Возвращает первый ключ словаря с значением True."""
        keys = PlaywrightJobManager._true_keys(value)
        return keys[0] if keys else None

    async def _build_search_query(self) -> List[Tuple[str, str]]:
        """Преобразует параметры поиска в список параметров URL hh.ru."""
        params = self.search_params
        query: List[Tuple[str, str]] = []

        text = params.get("keywords") or params.get("text") or params.get("job_title") or ""
        text = str(text).strip()
        if text:
            query.append(("text", text))
        for key in self._true_keys(params.get("search_field")):
            query.append(("search_field", key))
        words_to_exclude = str(params.get("words_to_exclude") or "").strip()
        if words_to_exclude:
            query.append(("excluded_text", words_to_exclude))

        # Регион, специализация, отрасль и районы задаются в URL по ID — берём их из справочников
        async with httpx.AsyncClient(
            base_url=HH_API_URL, headers={"User-Agent": HH_API_USER_AGENT}, timeout=15
        ) as client:
            area_ids = []
            for area in self._split_multi(params.get("area")):
                try:
                    items = await self._fetch_hh_dictionary(
                        client, "/suggests/areas", {"text": area}
                    )
                except httpx.HTTPError as e:
                    self._record_unapplied_filter("area", area, "dictionary_error", str(e))
                    continue
                area_id = self._best_match_id(area, [(i["id"], i["text"]) for i in items])
                if area_id:
                    area_ids.append(area_id)
                    query.append(("area", area_id))
                else:
                    self._record_unapplied_filter(
                        "area", area, "no_match", "Подходящий регион в справочнике не найден"
                    )

            # Названия специализаций и отраслей сами содержат запятые, поэтому значение одно
            role = str(params.get("professional_role") or "").strip()
            if role:
                try:
                    items = await self._fetch_hh_dictionary(
                        client, "/suggests/professional_roles", {"text": role}
                    )
                except httpx.HTTPError as e:
                    self._record_unapplied_filter(
                        "professional_role", role, "dictionary_error", str(e)
                    )
                else:
                    role_id = self._best_match_id(role, [(i["id"], i["text"]) for i in items])
                    if role_id:
                        query.append(("professional_role", role_id))
                    else:
                        self._record_unapplied_filter(
                            "professional_role",
                            role,
                            "no_match",
                            "Подходящая профессиональная роль в справочнике не найдена",
                        )

            industry = str(params.get("industry") or "").strip()
            if industry:
                try:
                    items = await self._fetch_hh_dictionary(client, "/industries")
                except httpx.HTTPError as e:
                    self._record_unapplied_filter("industry", industry, "dictionary_error", str(e))
                else:
                    options = []
                    for item in items:
                        options.append((item["id"], item["name"]))
                        options += [(sub["id"], sub["name"]) for sub in item.get("industries", [])]
                    industry_id = self._best_match_id(industry, options)
                    if industry_id:
                        query.append(("industry", industry_id))
                    else:
                        self._record_unapplied_filter(
                            "industry",
                            industry,
                            "no_match",
                            "Подходящая отрасль в справочнике не найдена",
                        )

            districts = self._split_multi(params.get("districts"))
            if districts:
                try:
                    items = await self._fetch_hh_dictionary(client, "/districts")
                except httpx.HTTPError as e:
                    for district in districts:
                        self._record_unapplied_filter(
                            "district", district, "dictionary_error", str(e)
                        )
                else:
                    options = [(i["id"], i["name"]) for i in items if i["area_id"] in area_ids]
                    for district in districts:
                        district_id = self._best_match_id(district, options)
                        if district_id:
                            query.append(("district", district_id))
                        else:
                            self._record_unapplied_filter(
                                "district",
                                district,
                                "no_match",
                                "Подходящий район в справочнике не найден",
                            )

        salary = params.get("salary")
        if salary:
            query.append(("salary", str(salary)))
        currency = self._first_true_key(params.get("currency"))
        if currency:
            query.append(("currency", currency))
        if params.get("only_with_salary") is True:
            query.append(("only_with_salary", "true"))

        education_mapping = {
            "not_needed": "not_required_or_not_specified",
            "middle": "special_secondary",
            "higher": "higher",
        }
        for key in self._true_keys(params.get("education")):
            query.append(("education", education_mapping[key]))

        experience = self._first_true_key(params.get("experience"))
        if experience:
            # В YAML используется doesntMatter, в hh.ru — doesNotMatter
            if experience == "doesntMatter":
                experience = "doesNotMatter"
            query.append(("experience", experience))

        for key in self._true_keys(params.get("employment")):
            if key == "ACCEPT_TEMPORARY":
                query.append(("accept_temporary", "true"))
            elif key == "INTERNSHIP":
                query.append(("label", "internship"))
            else:
                query.append(("employment_form", key))
        for key in self._true_keys(params.get("job_format")):
            query.append(("work_format", key))
        for key in self._true_keys(params.get("vacancy_label")):
            query.append(("label", key))

        order_by = self._first_true_key(params.get("order_by"))
        if order_by:
            query.append(("order_by", order_by))
        period_mapping = {
            "all_time": "0",
            "month": "30",
            "week": "7",
            "three_days": "3",
            "one_day": "1",
        }
        period = self._first_true_key(params.get("period"))
        if period:
            query.append(("search_period", period_mapping[period]))
        show = self._first_true_key(params.get("show"))
        if show:
            query.append(("items_on_page", show.removeprefix("show_")))

        return query

    @staticmethod
    async def _fetch_hh_dictionary(
        client: httpx.AsyncClient, path: str, params: Optional[Dict[str, str]] = None
    ) -> List[Any]:
        """Запрашивает справочник публичного API hh.ru.

        В отличие от устаревшего `_get_hh_dictionary`, при сетевой ошибке,
        некорректном HTTP-ответе или нечитаемом теле пробрасывает httpx.HTTPError,
        чтобы вызывающий код мог зафиксировать неприменённый фильтр, а не упасть.
        """
        try:
            response = await client.get(path, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError:
            raise
        except ValueError as e:
            # Тело ответа не является JSON — оборачиваем в тип, понятный вызывающему коду
            raise httpx.HTTPError(f"Некорректный JSON в ответе справочника {path}: {e}") from e
        return data.get("items", []) if isinstance(data, dict) else data

    @staticmethod
    async def _get_hh_dictionary(
        client: httpx.AsyncClient, path: str, params: Optional[Dict[str, str]] = None
    ) -> Any:
        """Устаревший помощник: запрос справочника, при ошибке возвращает пустой список.

        Сохранён для обратной совместимости; основной сбор использует
        `_fetch_hh_dictionary` и явно учитывает упавшие фильтры.
        """
        try:
            return await PlaywrightJobManager._fetch_hh_dictionary(client, path, params)
        except httpx.HTTPError as e:
            logger.warning(f"Не удалось получить справочник hh.ru {path} {params or ''}: {e}")
            return []

    def _record_unapplied_filter(
        self, filter_type: str, requested_value: str, reason: str, detail: str = ""
    ) -> None:
        """Сохраняет фильтр, который не удалось применить к URL поиска."""
        item = UnappliedFilter(
            filter_type=filter_type,
            requested_value=str(requested_value),
            reason=reason,
            detail=detail,
        )
        self.search_report.unapplied_filters.append(item)
        logger.warning(
            f"Фильтр поиска не применён: {filter_type}='{requested_value}' " f"({reason}: {detail})"
        )

    @staticmethod
    def _best_match_id(value: str, options: List[Tuple[str, str]]) -> Optional[str]:
        """Возвращает ID варианта с названием, ближайшим к value (по Левенштейну)."""
        if not options:
            logger.warning(f"Не найдено значение в справочнике hh.ru: {value}")
            return None
        value = value.strip().lower()
        best_id, best_name = min(options, key=lambda o: distance(value, o[1].lower()))
        logger.info(f"Фильтр поиска '{value}' сопоставлен с '{best_name}' (id={best_id})")
        return best_id

    async def get_vacancies_from_page(self, page_num: int = 0) -> List[Dict[str, Any]]:
        """Получить вакансии с очередной страницы.

        Возвращает только список успешно разобранных карточек в прежнем формате
        (name, id, alternate_url, employer). Детали сбора (ошибки навигации,
        неразобранные карточки) доступны через `fetch_vacancies_page` и
        `get_search_run_report`.
        """
        result = await self.fetch_vacancies_page(page_num)
        return result.vacancies

    async def fetch_vacancies_page(self, page_num: int = 0) -> PageFetchResult:
        """Собрать одну страницу выдачи с детальным итогом.

        Ошибка навигации на страницу не роняет сбор: возвращается результат с
        заполненным `error` и пустым списком вакансий. Ошибка разбора отдельной
        карточки не влияет на остальные карточки той же страницы.
        """
        result = PageFetchResult(page_num=page_num)
        try:
            await self._navigate_to_page(page_num)
        except Exception as e:
            result.error = f"Не удалось открыть страницу {page_num}: {e}"
            logger.warning(result.error)
            self._store_page_result(result)
            return result

        try:
            # Новый селектор после редизайна Magritte
            cards = await self.page.locator('[data-qa="vacancy-serp__vacancy"]').all()
        except Exception as e:
            result.error = f"Не удалось прочитать карточки на странице {page_num}: {e}"
            logger.warning(result.error)
            self._store_page_result(result)
            return result

        result.cards_count = len(cards)
        logger.info(f"Найдено {len(cards)} вакансий на странице {page_num}")

        for index, card in enumerate(cards):
            try:
                vac = await self._parse_vacancy_card(card)
            except Exception as e:
                reason = f"Ошибка разбора карточки #{index} на странице {page_num}: {e}"
                logger.warning(reason)
                result.failed_cards.append({"card_index": index, "error": reason})
                continue
            if vac:
                result.vacancies.append(vac)
                result.success_count += 1

        self._store_page_result(result)
        return result

    def _store_page_result(self, result: PageFetchResult) -> None:
        """Сохраняет итог страницы в сводке поиска (повторный вызов перезаписывает)."""
        pages = self.search_report.pages
        for idx, page in enumerate(pages):
            if page.page_num == result.page_num:
                pages[idx] = result
                return
        pages.append(result)

    async def _navigate_to_page(self, page_num: int) -> None:
        """Открывает нужную страницу выдачи, если текущий URL ей не соответствует.

        При невозможности построить URL или ошибке перехода пробрасывает исключение
        — вызывающий код фиксирует страницу как неудачную.
        """
        if not self.search_page_url:
            self.search_page_url = self.page.url

        if "hh.ru" not in self.search_page_url:
            return

        parsed = urllib.parse.urlparse(self.search_page_url)
        query = urllib.parse.parse_qs(parsed.query)
        current_page_param = query.get("page", ["0"])[0]

        if int(current_page_param) == page_num:
            return

        query["page"] = [str(page_num)]
        new_query = urllib.parse.urlencode(query, doseq=True)
        new_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
        self.search_page_url = new_url
        logger.info(f"Переходим на страницу {page_num}: {new_url}")
        await self.page.goto(new_url, wait_until="domcontentloaded")
        await self.pause_async(2, 3)

    async def _parse_vacancy_card(self, card: Locator) -> Optional[Dict[str, Any]]:
        """Парсит карточку вакансии из поисковой выдачи.

        Ошибки, несовместимые с разбором (нет заголовка/ссылки), возвращают None —
        такой элемент не считается вакансией. Иные исключения пробрасываются в
        `fetch_vacancies_page`, где фиксируются как ошибка конкретной карточки.
        """
        # Элемент заголовка
        title_el = card.locator('[data-qa="serp-item__title"]').first
        if await title_el.count() == 0:
            return None

        title = await get_clean_text(title_el)
        href = await title_el.get_attribute("href")
        if not href:
            return None

        # Полный URL
        if not href.startswith("http"):
            full_url = "https://hh.ru" + href
        else:
            full_url = href

        # ID вакансии
        vacancy_id = None
        # ID обычно находится в пути URL /vacancy/123456
        match = re.search(r"vacancy/(\d+)", full_url)
        if match:
            vacancy_id = match.group(1)

        # Работодатель
        employer_name = "Неизвестно"
        employer_id = None
        emp_el = card.locator('[data-qa="vacancy-serp__vacancy-employer"]').first
        if await emp_el.count() > 0:
            employer_name = await get_clean_text(emp_el)
            emp_href = await emp_el.get_attribute("href")
            if emp_href:
                match_emp = re.search(r"employer/(\d+)", emp_href)
                if match_emp:
                    employer_id = match_emp.group(1)

        return {
            "name": title,
            "id": vacancy_id,
            "alternate_url": full_url,
            "employer": {
                "id": employer_id,
                "name": employer_name,
            },
        }

    async def get_vacancy_full_info(self, vacancy_url: str) -> Dict[str, Any]:
        """Получает полную информацию о вакансии для LLM."""
        try:
            await self.page.goto(vacancy_url, wait_until="domcontentloaded")
        except PlaywrightError as e:
            # Переход может разово прерваться (например, net::ERR_ABORTED) — пробуем ещё раз
            logger.warning(f"Не удалось открыть страницу вакансии, повторяем попытку: {e}")
            await self.pause_async(2, 3)
            await self.page.goto(vacancy_url, wait_until="domcontentloaded")
        logger.info(f"Переход на страницу: {vacancy_url}")
        # После редиректов в URL страницы всегда есть ID вакансии
        vacancy_id_match = re.search(r"/vacancy/(\d+)", self.page.url)

        async def get_text_or_empty(selector: str) -> str:
            el = self.page.locator(selector)
            if await el.count() > 0:
                return await get_clean_text(el.first)
            return ""

        description = await get_text_or_empty('[data-qa="vacancy-description"]')

        # Навыки
        skills_list = []
        skills_els = self.page.locator('[data-qa="skills-element"]')
        count = await skills_els.count()
        for i in range(count):
            skills_list.append(await get_clean_text(skills_els.nth(i)))

        # Детали заголовка
        experience = await get_text_or_empty('[data-qa="work-experience-text"]')
        employment = await get_text_or_empty('[data-qa="common-employment-text"]')
        hiring_formats = await get_text_or_empty('[data-qa="vacancy-hiring-formats"]')
        schedule = await get_text_or_empty('[data-qa="work-schedule-by-days-text"]')
        working_hours = await get_text_or_empty('[data-qa="working-hours-text"]')
        work_formats = await get_text_or_empty('[data-qa="work-formats-text"]')

        # Зарплата
        salary = await get_text_or_empty('[data-qa="vacancy-salary"]')
        if not salary:
            # Откат по структуре сниппета (Magritte)
            salary = await get_text_or_empty("xpath=//div[contains(@class, 'vacancy-title')]/span")

        return {
            "salary": salary,
            "experience": experience,
            "employment": employment,
            "hiring_formats": hiring_formats,
            "schedule": schedule,
            "working_hours": working_hours,
            "work_formats": work_formats,
            "description": description,
            "skills": skills_list,
            "vacancy_id": vacancy_id_match.group(1) if vacancy_id_match else None,
        }

    async def _handle_interfering_messages(self) -> None:
        """Обрабатывает мешающие сообщения (куки, уведомления, попапы)."""
        message_was_processed = True

        while message_was_processed:
            await self.pause_async(2, 3)

            # Попап сбора дополнительных данных (опрос после отклика). Закрываем первым:
            # его оверлей перехватывает клики по остальным элементам страницы
            close_popup_btn = self.page.locator(
                '[data-qa="additional-data-collector__popup-close"]'
            )
            if await close_popup_btn.count() > 0:
                await close_popup_btn.first.click()
                break
            # Уведомления
            close_btn = self.page.locator('[data-qa="notification-close-button"]')
            if await close_btn.count() > 0:
                await close_btn.click()
                break
            # Куки
            cookies_btn = self.page.locator('[data-qa="cookies-policy-informer-accept"]')
            if await cookies_btn.count() > 0:
                await cookies_btn.click()
                break

            message_was_processed = False

    async def apply_to_vacancy(
        self, vacancy_url: str, cover_letter: str, gpt_answerer: Any, resume_component: Any
    ) -> Tuple[str, str]:
        """
        Откликается на вакансию. Возвращает (Результат, Сообщение).
        Результат: 'Success', 'Skip', 'Error', 'Limit'
        """
        if self.page.url != vacancy_url:
            await self.page.goto(vacancy_url, wait_until="domcontentloaded")
            logger.info(f"Переход на страницу: {vacancy_url}")

        await self.pause_async(1, 2)

        # Нажимаем "Откликнуться"
        apply_btn_top_selector = '[data-qa="vacancy-response-link-top"]'
        apply_btn_bottom_selector = '[data-qa="vacancy-response-link-bottom"]'

        logger.info("Жмем кнопку 'Откликнуться'")
        clicked = await safe_click(self.page, apply_btn_top_selector, click_all=True)
        if not clicked:
            clicked = await safe_click(self.page, apply_btn_bottom_selector, click_all=True)

        if not clicked:
            # Проверяем, был ли уже отклик или другое состояние
            return "Error", "Кнопка отклика не найдена"

        await self.pause_async(1, 2)

        if await self._select_resume(resume_component):
            logger.info("Выбрали резюме")

        # Ждём модального окна или перехода
        await self._handle_interfering_messages()

        # Обрабатываем вопросы
        questions_selector = '[data-qa="task-body"]'
        questions_count = await self.page.locator(questions_selector).count()
        if questions_count > 0:
            logger.info(f"Найдено {questions_count} вопросов")
            for i in range(questions_count):
                question_xpath = f"(//*[@data-qa='task-body'])[{i + 1}]"
                question_locator = self.page.locator(question_xpath)
                success, msg = await self._handle_question(
                    question_locator,
                    gpt_answerer,
                    resume_component,
                    question_selector=question_xpath,
                )
                if not success:
                    return "Skip", msg

        # Сопроводительное письмо вариант 1
        magritte_cl_form = self.page.locator('[data-qa="vacancy-response-letter-informer"]')
        if await magritte_cl_form.count() > 0:
            logger.info("Найдена форма сопроводительного письма (вариант 1)")
            await safe_fill(
                self.page,
                '[data-qa="vacancy-response-letter-informer"] textarea[name="text"]',
                cover_letter,
            )
            await self.pause_async(1, 2)
            # Отправляем сопроводительное письмо
            logger.info("Жмем кнопку отправки сопроводительного письма (вариант 1)")
            if await safe_click(
                self.page, '[data-qa="vacancy-response-letter-submit"]', timeout=10000
            ):
                await self.pause_async(2, 3)
                return "Success", "Сопроводительное письмо отправлено"

        # Сопроводительное письмо вариант 2
        cl_btn_xpath = "xpath=//*[text()='Добавить' or contains(text(), 'Сопроводительное')]"
        if await safe_click(
            self.page, '[data-qa="vacancy-response-letter-toggle"]', supress_warnings=True
        ):
            logger.info("Открыли форму сопроводительного письма (вариант 2)")
            await self.pause_async(1, 2)
        elif await self.page.locator(cl_btn_xpath).count() > 0:
            if await self.page.locator(cl_btn_xpath).first.is_visible():
                logger.info("Жмем кнопку открытия формы сопроводительного письма (вариант 2)")
                await safe_click(self.page, cl_btn_xpath, supress_warnings=True)
                await self.pause_async(1, 2)
        cl_input = self.page.locator('[data-qa="vacancy-response-popup-form-letter-input"]')
        if await cl_input.count() > 0:
            logger.info("Заполняем форму сопроводительного письма (вариант 2)")
            await safe_fill(
                self.page,
                '[data-qa="vacancy-response-popup-form-letter-input"]',
                cover_letter,
                supress_warnings=True,
            )
            await self.pause_async(1, 2)

        await self._handle_interfering_messages()

        # Проверяем, открыто ли окно с кнопкой отправки
        modal_submit_btn = self.page.locator('[data-qa="vacancy-response-submit-popup"]')
        if await modal_submit_btn.count() > 0 and await modal_submit_btn.is_visible():
            logger.info("Жмем кнопку отправки сопроводительного письма (вариант 2)")
            await safe_click(self.page, '[data-qa="vacancy-response-submit-popup"]')
            await self.pause_async(3, 4)
            await self._attach_cover_letter_on_success(cover_letter)
            return "Success", ""

        # Финальная отправка отклика. Кнопку ищем ТОЛЬКО внутри открытого модального окна,
        # иначе селектор цепляет кнопки 'Откликнуться' из блока похожих вакансий на странице
        # успеха и бот случайно откликается на чужую вакансию.
        submit_btn_selector = "[role='dialog'] >> xpath=.//*[text()='Откликнуться']"
        if await self.page.locator(submit_btn_selector).count() > 0:
            logger.info("Жмем кнопку 'Откликнуться'")
            await safe_click(self.page, submit_btn_selector)
            await self.pause_async(3, 4)
            await self._attach_cover_letter_on_success(cover_letter)
            return "Success", ""

        # Модального окна нет — отклик уже отправлен первым кликом (одиночное резюме без анкеты).
        # Проверяем признак успешного отклика, прикрепляем письмо и завершаем.
        if await self._response_succeeded():
            await self._attach_cover_letter_on_success(cover_letter)
            return "Success", ""

        return "Error", "Кнопка отправки не найдена"

    async def _response_succeeded(self) -> bool:
        """Проверяет, что отклик отправлен (на странице есть блок успешного отклика)."""
        success_selectors = [
            '[data-qa="responded-success-attach-cover-letter"]',
            '[data-qa="vacancy-response-link-view-topic"]',
        ]
        for selector in success_selectors:
            if await self.page.locator(selector).count() > 0:
                return True
        return False

    async def _attach_cover_letter_on_success(self, cover_letter: str) -> None:
        """Прикрепляет сопроводительное письмо на странице успешного отклика.

        После отправки отклика hh.ru может показать кнопку «Приложить сопроводительное
        письмо». Если она появилась, жмём её, вводим текст письма и отправляем его.
        Если кнопка не появилась (письмо уже приложено или другой сценарий) — выходим.
        """
        if not cover_letter:
            return

        attach_btn_selector = '[data-qa="responded-success-attach-cover-letter"]'
        attach_btn = self.page.locator(attach_btn_selector)
        try:
            await attach_btn.wait_for(state="visible", timeout=5000)
        except Exception:
            # Кнопка не появилась — письмо уже приложено или сценарий другой
            return

        logger.info("Найдена кнопка 'Приложить сопроводительное письмо' на странице успеха")
        if not await safe_click(self.page, attach_btn_selector, timeout=5000):
            return
        await self.pause_async(1, 2)

        # Заполняем поле письма в открывшемся модальном окне. Запасной селектор —
        # на случай иной разметки (при промахе safe_fill сохранит снимок).
        letter_selectors = [
            '[role="dialog"] textarea[name="text"]',
            'textarea[name="text"]',
        ]
        filled = False
        for selector in letter_selectors:
            if await safe_fill(
                self.page,
                selector,
                cover_letter,
                wait_for_timeout=5000,
                supress_warnings=True,
            ):
                filled = True
                break
        if not filled:
            logger.warning(
                "Не удалось найти поле ввода сопроводительного письма на странице успеха"
            )
            return
        await self.pause_async(1, 2)

        # Отправляем письмо кнопкой 'Отправить' модального окна.
        submit_selectors = [
            '[data-qa="vacancy-response-letter-submit"]',
            "xpath=//button[contains(., 'Отправить')]",
        ]
        for selector in submit_selectors:
            if await safe_click(self.page, selector, timeout=5000, supress_warnings=True):
                logger.info("Сопроводительное письмо приложено на странице успеха")
                await self.pause_async(2, 3)
                return
        logger.warning(
            "Не удалось найти кнопку отправки сопроводительного письма на странице успеха"
        )

    async def _select_resume(self, resume_component: Any) -> bool:
        """Выбирает резюме из списка. Возвращает True, если резюме было выбрано.

        Выбор резюме появляется только когда у пользователя несколько резюме. При
        единственном резюме триггер выбора отсутствует — это штатная ситуация, поэтому
        метод просто возвращает False без предупреждений.
        """
        if not self.page or not resume_component:
            return False

        target_title = (getattr(resume_component, "job_title", "") or "").strip()
        if not target_title:
            return False

        # Триггер выбора резюме присутствует только при наличии нескольких резюме.
        trigger_selectors = [
            "[data-qa='resume-title']",
            "xpath=//*[@data-qa='resume-title']",
        ]
        trigger_clicked = False
        for selector in trigger_selectors:
            try:
                if await safe_click(self.page, selector, timeout=2000, supress_warnings=True):
                    trigger_clicked = True
                    break
            except Exception:
                continue

        if not trigger_clicked:
            return False

        await self.pause_async(0.5, 1)

        # Список вариантов отображается как magritte select list
        options_locator = self.page.locator("[data-qa^='magritte-select-option-']")
        try:
            await options_locator.first.wait_for(state="visible", timeout=3000)
        except Exception:
            return False

        options = await options_locator.all()
        if not options:
            return False

        titles: List[str] = []
        for opt in options:
            title_el = opt.locator("[data-qa='resume-title'] [data-qa='cell-text-content']").first
            if await title_el.count() == 0:
                title_el = opt.locator("[data-qa='resume-title']").first
            title_text = (await title_el.text_content()) if await title_el.count() > 0 else ""
            title_text = re.sub(r"\s+", " ", (title_text or "")).strip()
            titles.append(title_text)

        # Выбираем наиболее близкое название
        target_norm = target_title.lower()
        best_idx = None
        best_dist = None
        for idx, title in enumerate(titles):
            if not title:
                continue
            d = distance(target_norm, title.lower())
            if best_dist is None or d < best_dist:
                best_dist = d
                best_idx = idx

        if best_idx is None:
            return False

        logger.info(f"Выбрано резюме для отклика: {titles[best_idx]}")

        await safe_click(self.page, "[data-qa^='magritte-select-option-']", element_number=best_idx)

        await self.pause_async(0.5, 1)

        # На странице с анкетой эта кнопка сразу отправляет отклик — её жмём после ответов
        if await self.page.locator('[data-qa="task-body"]').count() == 0:
            await safe_click(self.page, "[data-qa='vacancy-response-submit-popup']", timeout=10000)

        return True

    async def _handle_question(
        self,
        question: Locator,
        gpt_answerer: Any,
        resume_component: Any,
        question_selector: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Обрабатывает одиночный вопрос в анкете."""
        # 1. Извлекаем текст вопроса
        question_text_el = question.locator('[data-qa="task-question"]').first
        if await question_text_el.count() > 0:
            question_text = await get_clean_text(question_text_el)
        else:
            question_text = await get_clean_text(question)
        logger.info(f"Обрабатываем вопрос: {question_text}")

        # 2. Проверяем ячейки Magritte (Radio/Checkbox с метками)
        cells = await question.locator('[data-qa="cell"]').all()
        if cells:
            # Определяем тип по первой ячейке
            is_radio = await cells[0].locator('[data-qa="radio-container"]').count() > 0
            is_checkbox = await cells[0].locator('[data-qa="checkbox-container"]').count() > 0

            if is_radio or is_checkbox:
                options = []
                for cell in cells:
                    text_el = cell.locator('[data-qa="cell-text-content"]').first
                    text = await get_clean_text(text_el) if await text_el.count() > 0 else ""
                    options.append(text)

                options.append("Нет информации")

                if is_radio:
                    answer = gpt_answerer.select_one_answer_from_options(question_text, options)
                    clicked = False
                    for i, opt in enumerate(options):
                        if opt == answer and opt != "Нет информации":
                            await cells[i].click()
                            clicked = True
                            break
                    if clicked:
                        return True, ""
                    return False, "Подходящий ответ не найден"

                elif is_checkbox:
                    answers = gpt_answerer.select_many_answers_from_options(question_text, options)
                    clicked = False
                    for i, opt in enumerate(options):
                        if opt in answers and opt != "Нет информации":
                            await cells[i].click()
                            clicked = True
                    return clicked, "Подходящий ответ не найден" if not clicked else ""

        # 3. Radio (откат)
        radios = await question.locator('[data-qa="radio-container"]').all()
        if radios:
            options = []
            for r in radios:
                options.append(await get_clean_text(r))

            options.append("Нет информации")
            answer = gpt_answerer.select_one_answer_from_options(question_text, options)

            for i, opt in enumerate(options):
                if opt == answer and opt != "Нет информации":
                    if question_selector:
                        # Используем safe_click с индексом относительно вопроса
                        radio_xpath = f"{question_selector}//*[@data-qa='radio-container']"
                        await safe_click(self.page, radio_xpath, element_number=i)
                    else:
                        # Откат, если не передан селектор (не должно происходить при новом вызове)
                        await radios[i].click()
                    return True, ""
            return False, "Подходящий ответ не найден"

        # 4. Checkbox (откат)
        checkboxes = await question.locator('[data-qa="checkbox-container"]').all()
        if checkboxes:
            options = []
            for c in checkboxes:
                options.append(await get_clean_text(c))

            options.append("Нет информации")
            answers = gpt_answerer.select_many_answers_from_options(question_text, options)

            clicked = False
            for i, opt in enumerate(options):
                if opt in answers and opt != "Нет информации":
                    if question_selector:
                        checkbox_xpath = f"{question_selector}//*[@data-qa='checkbox-container']"
                        await safe_click(self.page, checkbox_xpath, element_number=i)
                    else:
                        await checkboxes[i].click()
                    clicked = True

            return clicked, "Подходящий ответ не найден" if not clicked else ""

        # 5. Textarea (внутри)
        textarea = question.locator("textarea")
        if await textarea.count() > 0:
            answer = gpt_answerer.answer_question_textual_wide_range(question_text)
            answer = resume_component.deanonymize_personal_information(answer)
            await textarea.fill(answer)
            return True, ""

        # 6. Textarea (соседний элемент - Magritte)
        if question_selector:
            sibling_textarea = self.page.locator(
                f"xpath={question_selector}/following-sibling::div[@data-qa='textarea-wrapper'][1]//textarea"
            )
            if await sibling_textarea.count() > 0:
                answer = gpt_answerer.answer_question_textual_wide_range(question_text)
                answer = resume_component.deanonymize_personal_information(answer)
                await sibling_textarea.fill(answer)
                return True, ""

        return False, "Неизвестный тип вопроса"

    async def get_my_resumes_from_browser(self) -> Dict[str, Any]:
        """Получает список резюме пользователя через браузер."""
        await self.ensure_logged_in()
        # Открываем страницу "Резюме и профиль" из главного меню
        menu_selector = '[data-qa="mainmenu_profileAndResumes"]'
        clicked = await safe_click(self.page, menu_selector, timeout=10000)
        if not clicked:
            await self.page.goto("https://hh.ru", wait_until="domcontentloaded")
            logger.info("Переход на страницу: https://hh.ru")
            await self.pause_async(1, 2)
            await safe_click(self.page, menu_selector, timeout=10000)
        # Ждём появления карточек резюме на странице
        try:
            await self.page.wait_for_selector(
                'a[data-qa^="resume-card-link-"][href*="/resume/"]',
                timeout=15000,
            )
        except Exception:
            logger.warning("Список резюме не найден после открытия страницы 'Резюме и профиль'.")
            return {"items": []}

        def _extract_resume_id_from_href(href: Optional[str]) -> Optional[str]:
            if not href:
                return None
            match = re.search(r"/resume/([a-zA-Z0-9]+)", href)
            if match:
                return match.group(1)
            match = re.search(r"[?&]resume=([a-zA-Z0-9]+)", href)
            if match:
                return match.group(1)
            return None

        resumes: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        links = await self.page.locator('a[data-qa^="resume-card-link-"][href*="/resume/"]').all()
        for link in links:
            title_el = link.locator('[data-qa="resume-title"] [data-qa="cell-text-content"]').first
            title = ((await title_el.text_content()) or "").strip()
            if not title:
                title_el = link.locator('[data-qa="resume-title"]').first
                title = ((await title_el.text_content()) or "").strip()

            href = await link.get_attribute("href")
            resume_id = _extract_resume_id_from_href(href)
            if not resume_id:
                continue

            if resume_id in seen_ids:
                continue
            seen_ids.add(resume_id)

            resumes.append({"id": resume_id, "title": title})

        logger.info(f"Найдено {len(resumes)} резюме")

        return {"items": resumes}

    async def get_resume_content_from_browser(self, resume_id: str) -> Dict[str, Any]:
        """
        Открывает страницу резюме hh.ru и парсит ключевые разделы.

        Сохраняет обратную совместимость, возвращая данные в формате API, когда это возможно,
        и всегда добавляет спарсенные разделы.
        """
        resume = {}

        user_profile_url = "https://hh.ru/applicant/profile/me"
        await self.page.goto(user_profile_url, wait_until="domcontentloaded")
        logger.info(f"Переход на страницу: {user_profile_url}")
        await self.pause_async(3, 4)

        resume["personal_information"] = {}
        resume["personal_information"]["first_name"] = await self._get_first_name()
        resume["personal_information"]["last_name"] = await self._get_last_name()
        resume["personal_information"]["birthday"] = await self._get_birthday()
        resume["personal_information"]["telegram"] = await self._get_telegram()
        resume["personal_information"]["whatsapp"] = await self._get_whatsapp()
        resume["area"] = await self._get_area()
        resume["driving_license"] = await self._get_driving_license()
        linkedin, habr_career = await self._get_other_links()
        if linkedin:
            resume["personal_information"]["linkedin"] = linkedin
        if habr_career:
            resume["personal_information"]["habr_career"] = habr_career
        # Отчество, дата рождения, пол и гражданство доступны только в форме редактирования профиля
        profile_edit_url = "https://hh.ru/profile/edit/common"
        await self.page.goto(profile_edit_url, wait_until="domcontentloaded")
        logger.info(f"Переход на страницу: {profile_edit_url}")
        await self.pause_async(2, 3)
        middle_name = await self._get_middle_name()
        birthday = await self._get_birthday()

        if middle_name:
            resume["personal_information"]["middle_name"] = middle_name
        if birthday:
            resume["personal_information"]["birthday"] = birthday
        (
            sex,
            citizenship,
            legal_auth,
        ) = await self._get_sex_citizenship_and_legal_auth()
        if sex:
            resume["personal_information"]["sex"] = sex
        if citizenship:
            resume["citizenship"] = citizenship
        if legal_auth:
            resume["legal_authorization"] = legal_auth

        resume_url = f"https://hh.ru/resume/{resume_id}"
        await self.page.goto(resume_url, wait_until="domcontentloaded")
        logger.info(f"Переход на страницу: {resume_url}")
        await self.pause_async(2, 3)

        resume["personal_information"]["phone"] = await self._get_resume_phone()
        resume["personal_information"]["email"] = await self._get_resume_email()
        resume["job_preferences"] = {}
        resume["job_preferences"]["job_type"] = await self._get_job_type()
        resume["job_preferences"]["job_format"] = await self._get_job_format()
        resume["job_preferences"]["time_to_travel"] = await self._get_time_to_travel()
        resume["job_preferences"][
            "readiness_to_job_trips"
        ] = await self._get_readiness_to_job_trips()
        resume["job_preferences"]["salary"] = await self._get_salary()
        resume["total_experience"] = await self._get_total_experience()
        resume["experience"] = await self._get_experience()
        resume["skills"] = await self._get_skills()
        resume["educations"] = await self._get_educations()
        resume["recommendations"] = await self._get_recommendations()
        resume["additional_education"] = await self._get_additional_education()
        resume["exams"] = await self._get_exams()
        resume["certificates"] = await self._get_certificates()
        await self.raise_resume()

        resume_url = f"https://hh.ru/resume/edit/{resume_id}/about"
        await self.page.goto(resume_url, wait_until="domcontentloaded")
        logger.info(f"Переход на страницу: {resume_url}")
        await self.pause_async(2, 3)
        resume["about_me"] = await self._get_about_me()
        return Resume(**resume).model_dump()

    async def raise_resume(self) -> None:
        """Поднимает резюме в поиске."""
        raise_btn_xpath = "xpath=//*[contains(text(), 'Поднять в') and contains(text(), 'поиске')]"
        if await safe_click(self.page, raise_btn_xpath):
            await self.pause_async(2, 3)
            logger.info("Резюме успешно поднято")
        else:
            logger.info("Резюме пока нельзя поднять")

    async def _get_first_name(self) -> str:
        """Получает имя из профиля."""
        first_name = self.page.locator('[data-qa="applicant-profile-common-firstname"]')
        if await first_name.count() == 0:
            return ""
        first_name = await first_name.first.text_content()
        first_name = sanitize_text(first_name, lowercase=False)
        return first_name

    async def _get_other_links(self) -> Tuple[str, str]:
        """Получает другие ссылки (LinkedIn, Habr Career)."""
        linkedin = ""
        habr_career = ""
        other_links = await self.page.locator(
            '[data-qa*="profile-other-communication-methods-card-row"]'
        ).all()
        for other_link in other_links:
            # Первая строка — сама ссылка, вторая — подпись (LinkedIn, career.habr.ru)
            text = (await other_link.inner_text()).strip().split("\n")[0]
            text = text.replace("\u2009", "").replace("\xa0", " ").strip()
            if "linkedin.com" in text:
                linkedin = text
            elif "habr.ru" in text:
                habr_career = text
        return linkedin, habr_career

    async def _get_middle_name(self) -> str:
        """Получает отчество из профиля."""
        middle_name = self.page.locator('[data-qa*="profile-common-edit-middleName"]')
        if await middle_name.count() == 0:
            return ""
        middle_name = await middle_name.first.get_attribute("value")
        middle_name = sanitize_text(middle_name, lowercase=False)
        return middle_name

    async def _get_birthday(self) -> str:
        """Получает дату рождения."""
        birthday = self.page.locator('[data-qa="profile-common-edit-birthday"]')
        if await birthday.count() == 0:
            return ""
        birthday = await birthday.first.get_attribute("value")
        birthday = sanitize_text(birthday)
        return birthday

    async def _get_sex_citizenship_and_legal_auth(self) -> Tuple[str, str, str]:
        """Получает пол, гражданство и разрешение на работу."""
        select_activators = await self.page.locator(
            '[data-qa="magritte-select-activator"], [data-qa="resume-gender-select-activator"]'
        ).all()
        sex = ""
        citizenship = ""
        work_permission = ""
        for select_activator in select_activators:
            text = await select_activator.inner_text()
            text = text.replace("\u2009", "").replace("\xa0", " ")
            if text.startswith("Пол"):
                sex = text.replace("Пол", "").strip()
            elif text.startswith("Гражданство"):
                citizenship = text.replace("Гражданство", "").strip()
            elif text.startswith("Разрешение на работу"):
                work_permission = text.replace("Разрешение на работу", "").strip()
        return sex, citizenship, work_permission

    async def _get_last_name(self) -> str:
        """Получает фамилию."""
        last_name = self.page.locator('[data-qa="applicant-profile-common-lastname"]')
        if await last_name.count() == 0:
            return ""
        last_name = await last_name.first.text_content()
        last_name = sanitize_text(last_name, lowercase=False)
        return last_name

    async def _get_telegram(self) -> str:
        """Получает Telegram из контактов."""
        telegram = self.page.locator("xpath=//*[contains(text(), 'Telegram')]")
        if await telegram.count() == 0:
            return ""
        parent = telegram.first.locator("../../../../../../../..")
        telegram = await parent.text_content()
        telegram = telegram.replace("Telegram", "").strip()
        return telegram

    async def _get_whatsapp(self) -> str:
        """Получает WhatsApp из контактов."""
        whatsapp = self.page.locator("xpath=//*[contains(text(), 'Whatsapp')]")
        if await whatsapp.count() == 0:
            return ""
        parent = whatsapp.first.locator("../../../../../../../..")
        whatsapp = await parent.text_content()
        whatsapp = whatsapp.replace("Whatsapp", "").strip()
        return whatsapp

    async def _get_area(self) -> str:
        """Получает местоположение (город)."""
        area = self.page.locator("xpath=//*[contains(text(), 'Где живёте')]")
        if await area.count() == 0:
            return ""
        parent = area.first.locator("../../../../../../../..")
        area = await parent.text_content()
        area = area.replace("Где живёте", "").strip()
        area = area.split("·")[0].strip()
        return area

    async def _get_driving_license(self) -> str:
        """Получает информацию о водительских правах."""
        driving_license = self.page.locator('[data-qa="profile-documents-vehicle-cell"]')
        if await driving_license.count() == 0:
            return ""
        driving_license = await driving_license.first.inner_text()
        # Первая строка — заголовок «Опыт вождения», далее категории прав и наличие машины
        lines = [line.strip() for line in driving_license.split("\n")[1:] if line.strip()]
        driving_license = sanitize_text(", ".join(lines))
        return driving_license

    async def _get_resume_phone(self) -> str:
        """Получает телефон из резюме."""
        phone = self.page.locator(
            '[data-qa="resume-contact-phone-value-text"], [data-qa="resume-contact-phone-value-preferred-text"]'
        )
        if await phone.count() == 0:
            return ""
        phone = await phone.first.text_content()
        phone = sanitize_text(phone)
        return phone

    async def _get_resume_email(self) -> str:
        """Получает email из резюме."""
        email = self.page.locator(
            '[data-qa="resume-contact-email-value-text"], [data-qa="resume-contact-email-value-preferred-text"]'
        )
        if await email.count() == 0:
            return ""
        email = await email.first.text_content()
        email = sanitize_text(email, lowercase=False)
        return email

    async def _get_salary(self) -> str:
        """Получает зарплату из резюме."""
        salary = self.page.locator('[data-qa="resume-block-salary"]')
        if await salary.count() == 0:
            return ""
        salary = await salary.first.text_content()
        salary = sanitize_text(salary)
        return salary

    async def _get_job_type(self) -> str:
        """Получает тип занятости из резюме."""
        job_type = self.page.locator("xpath=//*[contains(text(), 'Тип занятости:')]")
        if await job_type.count() == 0:
            return ""
        parent = job_type.first.locator("..")
        job_type = await parent.text_content()
        job_type = sanitize_text(job_type)
        job_type = job_type.split(":")[1].strip()
        return job_type

    async def _get_job_format(self) -> str:
        """Получает формат работы из резюме."""
        job_format = self.page.locator("xpath=//*[contains(text(), 'Формат работы:')]")
        if await job_format.count() == 0:
            return ""
        parent = job_format.first.locator("..")
        job_format = await parent.text_content()
        job_format = sanitize_text(job_format)
        job_format = job_format.split(":")[1].strip()
        return job_format

    async def _get_time_to_travel(self) -> str:
        """Получает желательное время в пути до работы."""
        time_to_travel = self.page.locator("xpath=//*[contains(text(), 'Желательное время')]")
        if await time_to_travel.count() == 0:
            return ""
        parent = time_to_travel.first.locator("..")
        time_to_travel = await parent.text_content()
        time_to_travel = sanitize_text(time_to_travel)
        time_to_travel = time_to_travel.split(":")[1].strip()
        return time_to_travel

    async def _get_readiness_to_job_trips(self) -> str:
        """Получает готовность к командировкам."""
        ready_to_job_trip = self.page.locator("xpath=//*[contains(text(), 'Командировки:')]")
        if await ready_to_job_trip.count() == 0:
            return ""
        parent = ready_to_job_trip.first.locator("..")
        ready_to_job_trip = await parent.text_content()
        ready_to_job_trip = sanitize_text(ready_to_job_trip)
        ready_to_job_trip = ready_to_job_trip.split(":")[1].strip()
        return ready_to_job_trip

    async def _get_total_experience(self) -> str:
        """Получает общий опыт работы."""
        total_experience = self.page.locator("xpath=//*[contains(text(), 'Опыт работы:')]")
        if await total_experience.count() == 0:
            return ""
        parent = total_experience.first.locator("..")
        total_experience = await parent.text_content()
        total_experience = sanitize_text(total_experience)
        total_experience = total_experience.split(":")[1].strip()
        return total_experience

    async def _get_experience(self) -> str:
        """Получает опыт работы (описание)."""
        experience = self.page.locator('[data-qa="resume-list-card-experience"]')
        group_locators = experience.locator('[class^="group--"]')
        experience_texts = await group_locators.all_text_contents()
        experience_texts = [
            text.replace("\u2009", "").replace("\xa0", " ") for text in experience_texts
        ]
        experience = "\n".join(experience_texts)
        return experience

    async def _get_skills(self) -> str:
        """Получает навыки."""
        skill_card = self.page.locator("[data-qa='skills-card']")
        skills = skill_card.locator('[class^="magritte-tag__label"]')
        skills = await skills.all_text_contents()
        skills = "\n".join(skills)
        return skills

    async def _get_educations(self) -> str:
        """Получает образование."""
        education_card = self.page.locator("[data-qa='resume-list-card-education']")
        educations = education_card.locator('[data-qa="cell-text-content"]')
        educations = await educations.all_text_contents()
        educations = "\n".join(educations)
        return educations

    async def _get_about_me(self) -> str:
        """Получает информацию 'Обо мне'."""
        about_me = self.page.locator("[data-qa='resume-editor-about']")
        about_me = await about_me.all_text_contents()
        about_me = "\n".join(about_me)
        return about_me

    async def _get_recommendations(self) -> str:
        """Получает рекомендации."""
        recommendations = self.page.locator("[data-qa='resume-list-card-recommendation']")
        recommendations_locator = recommendations.locator('[data-qa="cell-text-content"]')
        recommendations = await recommendations_locator.all_text_contents()
        recommendations = "\n".join(recommendations)
        return recommendations

    async def _get_additional_education(self) -> str:
        """Получает дополнительное образование."""
        additional_education = self.page.locator("[data-qa='resume-list-card-additionalEducation']")
        additional_education_locator = additional_education.locator('[data-qa="cell-text-content"]')
        additional_education = await additional_education_locator.all_text_contents()
        additional_education = "\n".join(additional_education)
        return additional_education

    async def _get_exams(self) -> str:
        """Получает информацию об экзаменах/тестах."""
        exams = self.page.locator("[data-qa='resume-list-card-attestationEducation']")
        exams_locator = exams.locator('[data-qa="cell-text-content"]')
        exams = await exams_locator.all_text_contents()
        exams = [
            r
            for r in exams
            if not (r == "Профориентация" or r.startswith("Тест поможет определить ваши"))
        ]
        exams = "\n".join(exams)
        return exams

    async def _get_certificates(self) -> str:
        """Получает сертификаты."""
        certificates = self.page.locator("[data-qa='resume-list-card-certificate']")
        certificates_locator = certificates.locator('[data-qa="cell-text-content"]')
        certificates = await certificates_locator.all_text_contents()
        certificates = "\n".join(certificates)
        return certificates
