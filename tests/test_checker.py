"""Tests for :mod:`amul_stock_watcher.checker` resilience to detail failures.

The real Selenium browser is replaced by a fake API client that reuses the
production :meth:`get_product_details_parallel` implementation on top of
dummy driver objects. Redis and Telegram are likewise replaced by in-memory
fakes.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Set, Tuple, cast

from selenium import webdriver

from amul_stock_watcher.api_client import AmulAPIClient
from amul_stock_watcher.checker import ProductAvailabilityChecker
from amul_stock_watcher.models import Product


class DummyDriver(webdriver.Chrome):
    """Stand-in for a Selenium Chrome instance that never starts a browser."""

    def __init__(self, label: str) -> None:
        # Deliberately skip webdriver.Chrome.__init__ (no browser process).
        self.label = label


class FakeAmulAPIClient(AmulAPIClient):
    """Real parallel fetch machinery, fake browser layer and list API."""

    def __init__(
        self,
        behaviors: Dict[str, Any],
        raw_products: List[Dict[str, Any]],
        pool_size: int = 3,
    ) -> None:
        self._driver_pool_lock = threading.Lock()
        self._driver_pool = [DummyDriver(f"pool-{i}") for i in range(pool_size)]
        self.driver = DummyDriver("main")
        self._behaviors = behaviors
        self._raw_products = raw_products
        self._create_driver = lambda: DummyDriver("created")  # type: ignore[method-assign]

    def set_store_preferences(self, store: str) -> bool:
        return True

    def get_store_from_pincode(self, pincode: str) -> str:
        return ""

    def get_products(self) -> List[Dict[str, Any]]:
        return self._raw_products

    def _get_product_details_with_driver(
        self, alias: str, driver: object
    ) -> Optional[Dict[str, Any]]:
        behavior = self._behaviors[alias]
        if isinstance(behavior, Exception):
            raise behavior
        return cast(Optional[Dict[str, Any]], behavior)


class FakeStateManager:
    """In-memory mirror of the Redis-backed state manager."""

    def __init__(self, initial_available: Set[str], store: str = "") -> None:
        self.store = store
        self._state: Dict[str, Set[str]] = {
            f"amul:{store}:available": set(initial_available)
        }
        self.updated: List[Tuple[str, Set[str]]] = []

    def get_previous_state(self, store: str) -> Set[str]:
        return set(self._state.get(f"amul:{store}:available", set()))

    def update_state(self, store: str, available_aliases: Set[str]) -> bool:
        key = f"amul:{store}:available"
        self._state[key] = set(available_aliases)
        self.updated.append((store, set(available_aliases)))
        return True

    def get_newly_available_products(
        self, current_products: List[Product]
    ) -> List[Product]:
        store = current_products[0].store
        current_available = {p.alias for p in current_products if p.available}
        previous_available = self.get_previous_state(store)
        newly_available = current_available - previous_available
        self.update_state(store, current_available)
        return [
            p
            for p in current_products
            if p.available and p.alias in newly_available
        ]


class FakeNotifier:
    """Records the notifications that would be sent to Telegram."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def send_notification(
        self,
        products: List[Product],
        force: bool = False,
        log_to_console: bool = False,
    ) -> bool:
        self.calls.append(
            {
                "products": list(products),
                "force": force,
                "log_to_console": log_to_console,
            }
        )
        return True


def _build_checker(
    api_client: AmulAPIClient,
    state_manager: Optional[FakeStateManager] = None,
) -> ProductAvailabilityChecker:
    checker = ProductAvailabilityChecker.__new__(ProductAvailabilityChecker)
    checker.api_client = api_client
    checker.notifier = FakeNotifier()  # type: ignore[assignment]
    checker.state_manager = state_manager  # type: ignore[assignment]
    checker.use_state_management = state_manager is not None
    checker._write_last_fetch_timestamp = lambda: None  # type: ignore[method-assign]
    return checker


def _raw_product(alias: str, available: int, price: float) -> Dict[str, Any]:
    return {
        "alias": alias,
        "name": f"Product {alias}",
        "available": available,
        "price": price,
    }


def _full_details(alias: str) -> Dict[str, Any]:
    return {
        "alias": alias,
        "inventory_quantity": 12,
        "weight": 500,
        "inventory_low_stock_quantity": 2,
        "total_order_count": 100,
        "compare_price": 300.0,
        "metafields": {"product_type": "new", "uom": "g"},
    }


def test_mixed_detail_failures_return_all_products_with_defaults() -> None:
    """One raising alias, one None alias, healthy aliases: run never breaks."""
    raw_products = [
        _raw_product("raises", available=1, price=100.0),
        _raw_product("missing", available=0, price=0.0),
        _raw_product("healthy", available=1, price=250.0),
    ]
    behaviors: Dict[str, Any] = {
        "raises": RuntimeError("detail page crashed"),
        "missing": None,
        "healthy": _full_details("healthy"),
    }
    api_client = FakeAmulAPIClient(behaviors, raw_products)
    checker = _build_checker(api_client)

    available, unavailable = checker.check_availability()
    all_products = available + unavailable

    # Every product survives; the run completes without raising.
    assert {p.alias for p in all_products} == {"raises", "missing", "healthy"}
    by_alias = {p.alias: p for p in all_products}

    # Failed fetches carry the safe defaults from _extract_detailed_info.
    for failed_alias in ("raises", "missing"):
        product = by_alias[failed_alias]
        assert product.inventory_quantity == 0
        assert product.weight == 0
        assert product.inventory_low_stock_quantity == 0
        assert product.total_order_count == 0
        assert product.compare_price == 0.0
        assert product.product_type == ""
        assert product.uom == ""

    # List-level fields are retained even when details are missing.
    assert by_alias["raises"].price == 100.0
    assert by_alias["raises"].available is True
    assert by_alias["missing"].available is False

    # Healthy product keeps its detailed values.
    healthy = by_alias["healthy"]
    assert healthy.inventory_quantity == 12
    assert healthy.weight == 500
    assert healthy.product_type == "new"
    assert healthy.uom == "g"
    assert healthy.compare_price == 300.0

    # Every borrowed dummy browser was returned to the pool.
    assert len(api_client._driver_pool) == 3


def test_missing_detail_previously_available_stays_available_without_notify() -> None:
    """A failed fetch must not turn a previously available product out of stock."""
    raw_products = [
        _raw_product("flaky", available=0, price=99.0),
        _raw_product("steady", available=1, price=120.0),
        _raw_product("gone", available=0, price=50.0),
    ]
    behaviors: Dict[str, Any] = {
        "flaky": RuntimeError("timeout"),
        "steady": _full_details("steady"),
        "gone": _full_details("gone"),
    }
    api_client = FakeAmulAPIClient(behaviors, raw_products)
    state_manager = FakeStateManager({"flaky", "steady"})
    checker = _build_checker(api_client, state_manager)

    available, unavailable = checker.check_availability()
    by_alias = {p.alias: p for p in available + unavailable}

    # Carry-over: list says unavailable, previous round said available.
    assert by_alias["flaky"].available is True
    assert by_alias["steady"].available is True
    assert by_alias["gone"].available is False
    assert {p.alias for p in available} == {"flaky", "steady"}

    # Full run: nothing is newly available -> no Telegram notification.
    checker.run(dry_run=True)
    notifier = cast(FakeNotifier, checker.notifier)
    assert notifier.calls == []

    # The baseline still contains the failed product (no pollution).
    assert state_manager.updated[-1][1] == {"flaky", "steady"}


def test_missing_detail_previously_unavailable_stays_unavailable() -> None:
    """A failed fetch cannot fabricate a restock for a previously absent item."""
    raw_products = [
        _raw_product("flaky", available=1, price=99.0),
        _raw_product("steady", available=1, price=120.0),
    ]
    behaviors: Dict[str, Any] = {
        "flaky": None,
        "steady": _full_details("steady"),
    }
    api_client = FakeAmulAPIClient(behaviors, raw_products)
    state_manager = FakeStateManager({"steady"})
    checker = _build_checker(api_client, state_manager)

    available, _unavailable = checker.check_availability()
    by_alias = {p.alias: p for p in available}

    # List says available but previous round had no stock: keep unavailable.
    assert "flaky" not in by_alias
    assert "steady" in by_alias

    checker.run(dry_run=True)
    notifier = cast(FakeNotifier, checker.notifier)
    assert notifier.calls == []
    assert state_manager.updated[-1][1] == {"steady"}


def test_all_details_failing_returns_full_list_and_logs_missing_count(
    caplog: Any,
) -> None:
    """Total detail failure still yields the complete product list."""
    raw_products = [
        _raw_product("one", available=1, price=10.0),
        _raw_product("two", available=0, price=20.0),
        _raw_product("three", available=1, price=30.0),
    ]
    behaviors: Dict[str, Any] = {
        "one": RuntimeError("boom-1"),
        "two": RuntimeError("boom-2"),
        "three": None,
    }
    api_client = FakeAmulAPIClient(behaviors, raw_products)
    checker = _build_checker(api_client)

    with caplog.at_level(logging.INFO):
        available, unavailable = checker.check_availability()

    all_products = available + unavailable
    assert len(all_products) == 3
    assert {p.alias for p in all_products} == {"one", "two", "three"}
    assert all(p.inventory_quantity == 0 for p in all_products)

    # Success/missing counts are logged.
    assert "0 succeeded, 3 missing out of 3 products" in caplog.text
    assert "Details missing for 3 product(s)" in caplog.text
    assert len(api_client._driver_pool) == 3


def test_successful_restock_still_triggers_notification() -> None:
    """Carry-over logic must not suppress genuinely newly available products."""
    raw_products = [
        _raw_product("old", available=1, price=10.0),
        _raw_product("restocked", available=1, price=20.0),
    ]
    behaviors: Dict[str, Any] = {
        "old": _full_details("old"),
        "restocked": _full_details("restocked"),
    }
    api_client = FakeAmulAPIClient(behaviors, raw_products)
    state_manager = FakeStateManager({"old"})
    checker = _build_checker(api_client, state_manager)

    checker.run(dry_run=True)
    notifier = cast(FakeNotifier, checker.notifier)

    assert len(notifier.calls) == 1
    notified = notifier.calls[0]["products"]
    assert [p.alias for p in notified] == ["restocked"]
    assert state_manager.updated[-1][1] == {"old", "restocked"}
