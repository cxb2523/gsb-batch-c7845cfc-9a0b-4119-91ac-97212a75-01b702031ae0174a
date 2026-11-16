from unittest.mock import AsyncMock, MagicMock

import pytest

from src.job_manager.playwright_manager import UnappliedFilter
from src.job_manager.search_customizer import SearchCustomizer


@pytest.fixture
def customizer():
    manager = MagicMock()
    manager.set_advanced_search_params = AsyncMock()
    return SearchCustomizer(manager)


@pytest.mark.asyncio
async def test_start_search_delegates_to_manager(customizer):
    customizer.set_search_parameters({"keywords": "Python"})

    await customizer.start_search()

    customizer.manager.set_advanced_search_params.assert_awaited_once_with({"keywords": "Python"})


def test_unapplied_filters_are_readable_from_search_component(customizer):
    """Верхний слой (бот из main.py) узнаёт о неприменённых фильтрах через компонент поиска."""
    customizer.manager.get_unapplied_filters.return_value = [
        UnappliedFilter(
            filter_type="area",
            requested_value="Несущеград",
            reason="no_match",
            detail="Подходящий регион в справочнике не найден",
        ),
        UnappliedFilter(
            filter_type="industry",
            requested_value="IT",
            reason="dictionary_error",
            detail="connection refused",
        ),
    ]

    unapplied = customizer.get_unapplied_filters()

    assert len(unapplied) == 2
    assert unapplied[0].filter_type == "area"
    assert unapplied[0].reason == "no_match"
    assert unapplied[1].reason == "dictionary_error"
    # Сериализация пригодна для логов и Telegram-уведомлений
    assert unapplied[0].to_dict()["requested_value"] == "Несущеград"
