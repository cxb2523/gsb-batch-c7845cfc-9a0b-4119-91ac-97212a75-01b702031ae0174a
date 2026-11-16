"""Offline tests for the /products/details assembly (api.product_details and
the HTTP layer in routes.py).

    python -m pytest shopee/test_api.py -q

The transports are replaced with the captured fixtures (shopee/fixtures/),
so the four cases below need no network:

  * product + shop both readable            -> 200, complete shop block, shop_status "ok"
  * product readable, shop temporarily down -> 200, product intact, shop degraded, shop_status "unavailable"
  * product readable, shop does not exist   -> 200, product intact, shop degraded, shop_status "not_found"
  * product does not exist                  -> 404 (never degraded)
  * product page fetch fails                -> 500 (never degraded)

The route layer is exercised through bottle's WSGI entrypoint, so the status
codes are the ones callers see.
"""
import io
import json
import os
import sys
from urllib.parse import urlencode

import bottle
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache  # noqa: E402
import routes  # noqa: E402,F401  (mounts the routes on bottle's default app)
from shopee import api  # noqa: E402
from shopee.fetch import ShopeeBlocked, ShopeeNotFound, ShopeeUpstreamError  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f) if name.endswith(".json") else f.read()


@pytest.fixture(autouse=True)
def clear_cache():
    cache._store.clear()
    yield
    cache._store.clear()


@pytest.fixture
def product_page(monkeypatch):
    """A readable product page; returns the recorder of calls."""
    calls = {"page": 0, "shop": 0, "vouchers": 0}

    def _page(country, shop_id, item_id):
        calls["page"] += 1
        return fixture("product_loreal_sg_trimmed.html")

    monkeypatch.setattr(api, "fetch_product_page", _page)
    return calls


@pytest.fixture
def shop_up(monkeypatch, product_page):
    """A readable shop profile + vouchers."""
    def _shop(country, shop_id=None, username=None):
        product_page["shop"] += 1
        return fixture("shop_base_loreal.json")

    def _vouchers(country, shop_id):
        product_page["vouchers"] += 1
        return fixture("shop_vouchers_loreal.json")

    monkeypatch.setattr(api, "fetch_shop_base", _shop)
    monkeypatch.setattr(api, "fetch_shop_vouchers", _vouchers)
    return product_page


def http_get(path, **query):
    """Call the mounted bottle app and return (status, parsed JSON)."""
    app = bottle.default_app()
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": urlencode(query),
        "wsgi.url_scheme": "http",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "CONTENT_TYPE": "",
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": io.StringIO(),
    }
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = int(status.split()[0])

    body = b"".join(app(environ, start_response))
    return captured["status"], json.loads(body.decode("utf-8"))


PRODUCT = "15235546.23326616342"


# ---- happy path ---------------------------------------------------------------------------

def test_product_and_shop_complete(shop_up):
    record = api.product_details(country="sg", product=PRODUCT)
    assert record["shop_status"] == "ok"
    assert record["name"].startswith("L'Oreal Paris Elseve Fall Resist")
    assert record["images"]
    assert record["shop"]["id"] == 15235546
    assert record["shop"]["name"] == "L'Oreal Paris Official Store"
    assert record["shop"]["username"] == "lorealparissg"
    assert record["shop"]["link"] == "https://shopee.sg/shop/15235546"
    assert record["shop"]["followers_count"] == 71543
    assert record["shop"]["rating"]["average"] == 4.89
    assert set(record["shop"]) == set(api.SHOP_BLOCK_FIELDS)
    assert shop_up["page"] == 1 and shop_up["shop"] == 1

    status, body = http_get("/products/details", product=PRODUCT)
    assert status == 200 and body["shop_status"] == "ok"
    assert body["shop"]["name"] == "L'Oreal Paris Official Store"


# ---- shop degraded ------------------------------------------------------------------------

def _assert_product_intact(record):
    assert record["id"] == 23326616342 and record["shop_id"] == 15235546
    assert record["name"].startswith("L'Oreal Paris Elseve Fall Resist")
    assert len(record["images"]) == 8
    assert record["attributes"] and record["categories"] and record["models"]


@pytest.mark.parametrize("error,expected", [
    (ShopeeUpstreamError("request failed: Timeout"), api.SHOP_STATUS_UNAVAILABLE),
    (ShopeeBlocked("HTTP 403 for shop 15235546"), api.SHOP_STATUS_UNAVAILABLE),
])
def test_shop_temporarily_failed_still_serves_product(monkeypatch, product_page, error, expected):
    def _shop(country, shop_id=None, username=None):
        raise error

    monkeypatch.setattr(api, "fetch_shop_base", _shop)

    record = api.product_details(country="sg", product=PRODUCT)
    _assert_product_intact(record)
    assert record["shop_status"] == expected
    assert record["shop"] == {
        "id": 15235546, "link": "https://shopee.sg/shop/15235546",
        "name": None, "username": None, "location": None, "is_official_shop": None,
        "is_shopee_verified": None, "is_preferred_plus_seller": None, "rating": None,
        "followers_count": None, "items_count": None, "response_rate_percent": None,
        "last_active_at": None, "joined_at": None,
    }

    status, body = http_get("/products/details", product=PRODUCT)
    assert status == 200
    assert body["shop_status"] == "unavailable"
    assert body["name"] == record["name"] and body["shop"]["id"] == 15235546
    assert body["shop"]["name"] is None


def test_shop_not_found_still_serves_product(monkeypatch, product_page):
    def _shop(country, shop_id=None, username=None):
        raise ShopeeNotFound("shop 15235546 not found")

    monkeypatch.setattr(api, "fetch_shop_base", _shop)

    record = api.product_details(country="sg", product=PRODUCT)
    _assert_product_intact(record)
    assert record["shop_status"] == api.SHOP_STATUS_NOT_FOUND
    assert record["shop"]["id"] == 15235546
    assert record["shop"]["link"] == "https://shopee.sg/shop/15235546"
    assert record["shop"]["username"] is None and record["shop"]["rating"] is None

    status, body = http_get("/products/details", product=PRODUCT)
    assert status == 200 and body["shop_status"] == "not_found"


def test_shop_parse_without_id_is_not_found(monkeypatch, product_page):
    """get_shop_base answers an error payload that parses to an empty record:
    _shop_record raises ShopeeNotFound, which the product degrades around."""
    def _shop(country, shop_id=None, username=None):
        product_page["shop"] += 1
        return fixture("shop_base_unknown.json")

    monkeypatch.setattr(api, "fetch_shop_base", _shop)

    record = api.product_details(country="sg", product=PRODUCT)
    assert record["shop_status"] == api.SHOP_STATUS_NOT_FOUND
    assert record["name"].startswith("L'Oreal Paris Elseve")


# ---- product decides the outcome ----------------------------------------------------------

def test_product_not_found_is_404(monkeypatch, shop_up):
    monkeypatch.setattr(api, "fetch_product_page",
                        lambda country, shop_id, item_id: fixture("product_unknown_sg_trimmed.html"))

    status, body = http_get("/products/details", product="15235546.99999999999")
    assert status == 404
    assert "not found" in body["error"]


def test_product_fetch_failure_is_500_not_degraded(monkeypatch, shop_up):
    def _page(country, shop_id, item_id):
        raise ShopeeUpstreamError("request failed: Timeout")

    monkeypatch.setattr(api, "fetch_product_page", _page)

    status, body = http_get("/products/details", product=PRODUCT)
    assert status == 500
    assert "failed" in body["error"]


def test_product_block_is_500(monkeypatch, shop_up):
    def _page(country, shop_id, item_id):
        raise ShopeeBlocked("product page served without its data blob")

    monkeypatch.setattr(api, "fetch_product_page", _page)

    status, body = http_get("/products/details", product=PRODUCT)
    assert status == 500


# ---- caching ------------------------------------------------------------------------------

def test_shop_cache_hit_does_not_refetch(shop_up):
    api.product_details(country="sg", product=PRODUCT)
    api.product_details(country="sg", product=PRODUCT)
    assert shop_up["shop"] == 1 and shop_up["vouchers"] == 1
    assert shop_up["page"] == 1           # the product record is cached too


def test_degraded_shop_is_not_cached(monkeypatch, product_page):
    """A failed shop read must not be remembered: the next call retries."""
    state = {"fail": True, "shop_calls": 0}

    def _shop(country, shop_id=None, username=None):
        state["shop_calls"] += 1
        if state["fail"]:
            raise ShopeeUpstreamError("request failed: Timeout")
        return fixture("shop_base_loreal.json")

    monkeypatch.setattr(api, "fetch_shop_base", _shop)
    monkeypatch.setattr(api, "fetch_shop_vouchers", lambda country, shop_id: {"data": []})

    first = api.product_details(country="sg", product=PRODUCT)
    assert first["shop_status"] == api.SHOP_STATUS_UNAVAILABLE
    state["fail"] = False
    second = api.product_details(country="sg", product=PRODUCT)
    assert second["shop_status"] == "ok" and second["shop"]["name"] == "L'Oreal Paris Official Store"
    assert state["shop_calls"] == 2
