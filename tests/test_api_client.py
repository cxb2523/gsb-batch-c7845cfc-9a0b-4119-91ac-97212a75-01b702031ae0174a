"""Tests for :mod:`amul_stock_watcher.api_client` parallel detail fetching.

No real browser is started: ``AmulAPIClient`` is built without running its
Selenium-based ``__init__`` and the driver pool is populated with plain
objects.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from selenium import webdriver

from amul_stock_watcher.api_client import AmulAPIClient


class DummyDriver(webdriver.Chrome):
    """Stand-in for a Selenium Chrome instance that never starts a browser."""

    def __init__(self, label: str) -> None:
        # Deliberately skip webdriver.Chrome.__init__ (no browser process).
        self.label = label
        self.quit_called = False

    def quit(self) -> None:
        self.quit_called = True


def _make_client() -> AmulAPIClient:
    """Build a client whose browser layer never touches a real browser."""
    client = AmulAPIClient.__new__(AmulAPIClient)
    client._driver_pool_lock = threading.Lock()
    client._driver_pool = []
    client.driver = DummyDriver("main")
    client._create_driver = lambda: DummyDriver("created")  # type: ignore[method-assign]
    return client


def test_parallel_contain_failures_and_returns_drivers() -> None:
    """A raising alias and a None alias must not interrupt the other fetches."""
    client = _make_client()
    seeded_drivers = [DummyDriver("pool-1"), DummyDriver("pool-2"), DummyDriver("pool-3")]
    client._driver_pool.extend(seeded_drivers)

    def fake_fetch(alias: str, driver: object) -> Optional[Dict[str, Any]]:
        if alias == "boom":
            raise RuntimeError("browser exploded")
        if alias == "empty":
            return None
        return {"alias": alias, "inventory_quantity": 5}

    client._get_product_details_with_driver = fake_fetch  # type: ignore[method-assign]

    results = client.get_product_details_parallel(
        ["boom", "empty", "ok-1", "ok-2"], max_workers=3
    )

    # Structure stays Dict[alias, Optional[dict]] with every alias present.
    assert set(results.keys()) == {"boom", "empty", "ok-1", "ok-2"}
    assert results["boom"] is None
    assert results["empty"] is None
    assert results["ok-1"] == {"alias": "ok-1", "inventory_quantity": 5}
    assert results["ok-2"] == {"alias": "ok-2", "inventory_quantity": 5}

    # Every borrowed driver must be back in the pool, none quit/discarded.
    assert len(client._driver_pool) == len(seeded_drivers)
    assert set(client._driver_pool) == set(seeded_drivers)
    assert all(not driver.quit_called for driver in seeded_drivers)


def test_parallel_survives_driver_pool_acquisition_failure() -> None:
    """Exceptions raised before a driver is borrowed must also be contained."""
    client = _make_client()

    def boom_pool() -> webdriver.Chrome:
        raise RuntimeError("pool exhausted")

    client._get_driver_from_pool = boom_pool  # type: ignore[method-assign]

    results: Dict[str, Optional[Dict[str, Any]]] = client.get_product_details_parallel(
        ["a", "b"], max_workers=2
    )

    assert results == {"a": None, "b": None}


def test_parallel_creates_and_returns_new_drivers() -> None:
    """Drivers created on demand are returned to the pool on success/failure."""
    client = _make_client()
    created: List[DummyDriver] = []

    def create_driver() -> DummyDriver:
        driver = DummyDriver(f"created-{len(created)}")
        created.append(driver)
        return driver

    client._create_driver = create_driver  # type: ignore[method-assign]

    def fake_fetch(alias: str, driver: object) -> Optional[Dict[str, Any]]:
        if alias == "boom":
            raise RuntimeError("detail page crashed")
        return {"alias": alias}

    client._get_product_details_with_driver = fake_fetch  # type: ignore[method-assign]

    results = client.get_product_details_parallel(["boom", "ok"], max_workers=2)

    assert results == {"boom": None, "ok": {"alias": "ok"}}
    # At least one driver had to be created; none may be lost or quit.
    assert created, "expected on-demand driver creation"
    assert all(driver in client._driver_pool for driver in created)
    assert all(not driver.quit_called for driver in created)


def test_parallel_empty_aliases() -> None:
    """An empty input produces an empty mapping without touching the pool."""
    client = _make_client()

    assert client.get_product_details_parallel([]) == {}
    assert client._driver_pool == []
