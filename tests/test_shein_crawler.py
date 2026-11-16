"""Acceptance test for the per-category settlement refactor of SheinCrawler.

Simulates three categories with a fake Selenium driver:
  - catA: scrapes 2 pages, page 2 overlaps page 1 (duplicate product_id)
          and contains one product that fails to parse.
  - catB: contains a product already sent by catA (cross-category
          duplicate), then throws on the pagination click.
  - catC: driver.get() raises immediately (page cannot be opened).

Run from the repo root:  python tests/test_shein_crawler.py
"""

import json
import logging
import os
import sys
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# --- Stub db modules so no config.ini / sqlalchemy / real DB is needed ---
db_database = types.ModuleType('db.database')
db_database.DATABASE_URL = 'sqlite:///:memory:'
db_manager = types.ModuleType('db.manager')


class _DummyDatabaseManager:
    def __init__(self, *args, **kwargs):
        pass


db_manager.DatabaseManager = _DummyDatabaseManager
sys.modules['db.database'] = db_database
sys.modules['db.manager'] = db_manager

from selenium.common.exceptions import NoSuchElementException  # noqa: E402

import crawler.shein_crawler as shein_module  # noqa: E402
from crawler.shein_crawler import SheinCrawler  # noqa: E402
from logs.logger import logger  # noqa: E402

# No real sleeping / no real JS execution during the test
shein_module.wait_for = lambda seconds: None
shein_module.disable_element_by_class = lambda *a, **k: True

URL_BASE = 'https://br.shein.com/'
URL_A = 'https://br.shein.com/cat-a-c-1.html'
URL_B = 'https://br.shein.com/cat-b-c-2.html'
URL_C = 'https://br.shein.com/cat-c-c-3.html'


class FakeElement:
    def __init__(self, attrs=None, on_click=None):
        self.attrs = attrs or {}
        self.on_click = on_click

    def get_attribute(self, name):
        return self.attrs.get(name)

    def click(self):
        if self.on_click:
            self.on_click()

    def send_keys(self, *args):
        pass


class FakeSecao:
    """A section[role='listitem'] element wrapping one product."""

    def __init__(self, product_id, broken=False):
        self.broken = broken
        self.tag_a = FakeElement({
            'data-id': product_id,
            'href': f'https://br.shein.com/p/{product_id}.html',
            'data-title': f'Product {product_id}',
            'data-price': '10.0',
            'data-cat_id': 'cat',
            'data-sku': f'sku-{product_id}',
            'data-spu': f'spu-{product_id}',
            'data-us-price': '10.0',
            'data-us-origin-price': '12.0',
            'data-discount': '0',
        })

    def find_element(self, by, value):
        if self.broken:
            raise RuntimeError('broken product element')
        if value == 'a':
            return self.tag_a
        raise NoSuchElementException(value)

    def find_elements(self, by, value):
        return []  # no images


class FakeDriver:
    """Serves scripted pages per category URL."""

    def __init__(self, config):
        self.config = config
        self.current = None
        self.page_index = 0

    def get(self, url):
        cfg = self.config.get(url)
        if cfg is None:  # base url etc.
            self.current = None
            return
        if cfg.get('fail_on_get'):
            raise RuntimeError(f'cannot open page {url}')
        self.current = cfg
        self.page_index = 0

    def find_element(self, by, value):
        if value == 'body':
            return FakeElement()
        if value == 'sui-pagination__next':
            # has_next_page(): enabled while there are pages left to process
            disabled = (
                'false'
                if self.current and self.page_index < len(self.current['pages'])
                else 'true'
            )
            return FakeElement({'disabled': disabled})
        if value == '.sui-pagination__next.sui-pagination__btn':
            def advance():
                if self.current.get('click_error'):
                    raise self.current['click_error']
                self.page_index += 1
            return FakeElement(on_click=advance)
        raise NoSuchElementException(value)

    def find_elements(self, by, value):
        if value == 'section[role="listitem"]' and self.current:
            return [
                FakeSecao(pid, broken=(pid == 'BROKEN'))
                for pid in self.current['pages'][self.page_index]
            ]
        return []

    def quit(self):
        pass


class FakeKafkaProducer:
    def __init__(self):
        self.messages = []

    def produce(self, topic, value):
        self.messages.append((topic, value))


def main():
    config = {
        # catA: 2 pages, p2 appears on both pages, one broken product
        URL_A: {'pages': [['p1', 'p2', 'BROKEN'], ['p2', 'p3']]},
        # catB: p3 already sent by catA; pagination click throws
        URL_B: {
            'pages': [['p3', 'p4']],
            'click_error': RuntimeError('pagination click failed'),
        },
        # catC: page cannot even be opened
        URL_C: {'fail_on_get': True},
    }

    crawler = SheinCrawler.__new__(SheinCrawler)
    crawler.driver = FakeDriver(config)
    crawler.kafka_producer = FakeKafkaProducer()
    crawler.url_base = URL_BASE
    crawler.links_category = [URL_A, URL_B, URL_C]
    crawler.list_category = ['catA', 'catB', 'catC']
    crawler._sent_product_ids = set()
    crawler.clic_icone = lambda: None
    crawler.get_links_category = lambda: None
    crawler.extract_category_from_url = lambda: None

    # Capture log records to inspect the summaries
    records = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger.logger.addHandler(ListHandler())

    crawler.run()

    summaries = [r for r in records if r.startswith('[SUMMARY]')]
    totals = [r for r in records if r.startswith('[TOTAL]')]

    print('\n--- captured summaries ---')
    for line in summaries + totals:
        print(line)
    print('--------------------------\n')

    sent_ids = [
        json.loads(value)['product_id']
        for _, value in crawler.kafka_producer.messages
    ]

    # 1. Normal category sends its products, duplicate sent only once,
    #    broken product does not abort the page.
    assert sent_ids == ['p1', 'p2', 'p3', 'p4'], sent_ids

    # 2. Per-category summaries
    assert len(summaries) == 3, summaries
    assert (
        'category=catA' in summaries[0]
        and 'sent=3' in summaries[0]
        and 'duplicates_skipped=1' in summaries[0]
        and 'parse_errors=1' in summaries[0]
        and 'failed=False' in summaries[0]
    ), summaries[0]
    assert (
        'category=catB' in summaries[1]
        and 'sent=1' in summaries[1]
        and 'duplicates_skipped=1' in summaries[1]
        and 'failed=True' in summaries[1]
    ), summaries[1]
    assert (
        'category=catC' in summaries[2]
        and 'sent=0' in summaries[2]
        and 'failed=True' in summaries[2]
    ), summaries[2]

    # 3. Total line: 1 ok, 2 failed, 4 products overall
    assert len(totals) == 1, totals
    assert (
        'categories_ok=1' in totals[0]
        and 'categories_failed=2' in totals[0]
        and 'total_products_sent=4' in totals[0]
    ), totals[0]

    # 4. Total equals the sum of per-category sent counts and the
    #    actual number of Kafka messages.
    sent_sum = sum(
        int(s.split('sent=')[1].split()[0]) for s in summaries
    )
    assert sent_sum == 4 == len(crawler.kafka_producer.messages)

    print('ALL ASSERTIONS PASSED')


if __name__ == '__main__':
    main()
