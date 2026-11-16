"""Offline tests for the spider's parsing/assembly and the exporter's sorting.

All fixtures are built locally: open-data JSON dicts and React Flight (RSC)
text payloads. No network access is involved.
"""

import datetime
import io
import json
import unittest

from scrapy.http import TextResponse

from czech_political_parties.exporters import SortedJsonItemExporter, sort_key
from czech_political_parties.spiders import (
    ACTIVE_STATE,
    LIST_URL,
    CzechPoliticalPartiesSpider,
)

FULL_ITEM_KEYS = {
    'name', 'code', 'id', 'reg_number', 'reg_date',
    'address', 'people', 'type', 'is_active',
}


def open_data_record(party_id, name, code='X', reg_date='2020-01-15', **overrides):
    record = {
        'název': name,
        'zkratka': code,
        'identifikační_číslo': '12345678',
        'číslo_registrace': f'VS/{party_id}/2020',
        'den_registrace': reg_date,
        'adresa_sídla': 'Praha 1, Testovací 1',
        'url': f'https://mv.gov.cz/soubor/detail.aspx?id={party_id}',
        'osoby': [
            {'jméno': '  Jan Novák  ', 'funkce': 'předseda:'},
            {'jméno': '   ', 'funkce': 'místopředseda:'},  # blank -> skipped
        ],
    }
    for key, value in overrides.items():
        if value is ...:
            del record[key]
        else:
            record[key] = value
    return record


def rsc_text(records, item_count):
    """A minimal React Flight payload carrying the given list records."""
    body = json.dumps({
        'politickeStranyList': records,
        'pagingInfo': {'pageNo': 1, 'pageSize': 1000, 'itemCount': item_count},
    }, ensure_ascii=False, separators=(',', ':'))
    return f'0:["$","$L1",null,{{"data":{body}}}]\n'


def rsc_response(text, states='1,2,3,4'):
    url = f'{LIST_URL}?Stavy={states}&PageSize=1000&PageNo=1'
    return TextResponse(url=url, body=text.encode('utf-8'), encoding='utf-8')


def run_spider(parties, all_states_rsc, active_rsc):
    """Drive the spider's parse callbacks the way Scrapy would."""
    spider = CzechPoliticalPartiesSpider()
    requests = list(spider.parse_types(rsc_response(all_states_rsc), parties=parties))
    assert len(requests) == 1
    request = requests[0]
    assert f'Stavy={ACTIVE_STATE}' in request.url
    return list(spider.parse_active(rsc_response(active_rsc, ACTIVE_STATE),
                                    **request.cb_kwargs))


class FullReturnTest(unittest.TestCase):
    """A complete RSC list enriches every record exactly as before."""

    def test_full_enrichment(self):
        parties = [
            open_data_record(1, 'Strana A'),
            open_data_record(2, 'Hnutí B'),
            open_data_record(3, 'Strana C'),
        ]
        all_states = rsc_text(
            [{'id': 1, 'typ': 0}, {'id': 2, 'typ': 1}, {'id': 3, 'typ': 0}],
            item_count=3,
        )
        active = rsc_text([{'id': 1, 'typ': 0}, {'id': 3, 'typ': 0}], item_count=2)

        items = run_spider(parties, all_states, active)

        self.assertEqual(len(items), 3)
        by_name = {item['name']: item for item in items}
        self.assertEqual(by_name['Strana A']['type'], 'party')
        self.assertIs(by_name['Strana A']['is_active'], True)
        self.assertEqual(by_name['Hnutí B']['type'], 'movement')
        self.assertIs(by_name['Hnutí B']['is_active'], False)
        self.assertEqual(by_name['Strana C']['type'], 'party')
        self.assertIs(by_name['Strana C']['is_active'], True)

    def test_output_structure_unchanged(self):
        parties = [open_data_record(1, 'Strana A')]
        all_states = rsc_text([{'id': 1, 'typ': 0}], item_count=1)
        active = rsc_text([{'id': 1, 'typ': 0}], item_count=1)

        (item,) = run_spider(parties, all_states, active)

        # Exactly the original fields — no degradation marker, nothing renamed.
        self.assertEqual(set(item), FULL_ITEM_KEYS)
        self.assertNotIn('partial', item)
        self.assertEqual(item['code'], 'X')
        self.assertEqual(item['id'], '12345678')
        self.assertEqual(item['reg_number'], 'VS/1/2020')
        self.assertEqual(item['reg_date'], datetime.date(2020, 1, 15))
        self.assertEqual(item['address'], 'Praha 1, Testovací 1')
        self.assertEqual(
            item['people'], [{'name': 'Jan Novák', 'role': 'předseda'}]
        )


class TruncatedListTest(unittest.TestCase):
    """A truncated RSC list degrades only the missing records."""

    def test_truncated_types_list(self):
        parties = [
            open_data_record(1, 'Strana A'),
            open_data_record(2, 'Hnutí B'),
            open_data_record(3, 'Strana C'),
        ]
        # The endpoint claims 3 records but returns only the first.
        all_states = rsc_text([{'id': 1, 'typ': 0}], item_count=3)
        active = rsc_text([{'id': 1, 'typ': 0}], item_count=1)

        items = run_spider(parties, all_states, active)

        self.assertEqual(len(items), 3)  # nothing dropped, no exception
        by_name = {item['name']: item for item in items}

        fetched = by_name['Strana A']
        self.assertEqual(fetched['type'], 'party')
        self.assertIs(fetched['is_active'], True)
        self.assertNotIn('partial', fetched)

        for name in ('Hnutí B', 'Strana C'):
            missing = by_name[name]
            self.assertIsNone(missing['type'])
            # The active list was complete, so absence is a reliable False.
            self.assertIs(missing['is_active'], False)
            self.assertIs(missing['partial'], True)
            # Open-data fields are still exported as usual.
            self.assertEqual(missing['code'], 'X')
            self.assertEqual(missing['id'], '12345678')

    def test_truncated_active_list(self):
        parties = [open_data_record(1, 'Strana A'), open_data_record(2, 'Hnutí B')]
        all_states = rsc_text([{'id': 1, 'typ': 0}, {'id': 2, 'typ': 1}], item_count=2)
        # The active list is truncated: id 2 may or may not be active.
        active = rsc_text([{'id': 1, 'typ': 0}], item_count=2)

        items = run_spider(parties, all_states, active)

        by_name = {item['name']: item for item in items}
        self.assertIs(by_name['Strana A']['is_active'], True)
        self.assertNotIn('partial', by_name['Strana A'])

        unknown = by_name['Hnutí B']
        self.assertEqual(unknown['type'], 'movement')  # known from full list
        self.assertIsNone(unknown['is_active'])  # unknown, not guessed
        self.assertIs(unknown['partial'], True)


class MissingFieldsTest(unittest.TestCase):
    """A single broken record degrades only its own uncomputable fields."""

    def test_missing_url_typ_and_osoby(self):
        parties = [
            open_data_record(1, 'Bez URL', url=...),
            open_data_record(2, 'Bez typu'),
            open_data_record(3, 'Bez osob', osoby=...),
        ]
        all_states = rsc_text(
            [
                {'id': 1, 'typ': 0},
                {'id': 2},  # no `typ`
                {'id': 3, 'typ': 1},
                {'typ': 0},  # no `id` — junk record, must not abort the batch
            ],
            item_count=4,
        )
        active = rsc_text([{'id': 2}, {'id': 3, 'typ': 1}], item_count=2)

        items = run_spider(parties, all_states, active)

        self.assertEqual(len(items), 3)
        by_name = {item['name']: item for item in items}

        no_url = by_name['Bez URL']
        self.assertIsNone(no_url['type'])
        self.assertIsNone(no_url['is_active'])
        self.assertIs(no_url['partial'], True)
        # Everything computable from the open data is still there.
        self.assertEqual(no_url['code'], 'X')
        self.assertEqual(no_url['people'], [{'name': 'Jan Novák', 'role': 'předseda'}])

        no_typ = by_name['Bez typu']
        self.assertIsNone(no_typ['type'])
        self.assertIs(no_typ['is_active'], True)  # id 2 is in the active list
        self.assertIs(no_typ['partial'], True)

        no_osoby = by_name['Bez osob']
        self.assertEqual(no_osoby['type'], 'movement')
        self.assertIs(no_osoby['is_active'], True)
        self.assertEqual(no_osoby['people'], [])
        self.assertIs(no_osoby['partial'], True)


class ExporterSortingTest(unittest.TestCase):
    """The exporter keeps the original ordering: reg_date desc, then name."""

    def export(self, items):
        file = io.BytesIO()
        exporter = SortedJsonItemExporter(file)
        exporter.start_exporting()
        for item in items:
            exporter.export_item(item)
        exporter.finish_exporting()
        return json.loads(file.getvalue().decode('utf-8'))

    def test_sort_key(self):
        older = {'reg_date': datetime.date(2019, 5, 1), 'name': 'Zebra'}
        newer = {'reg_date': datetime.date(2021, 1, 1), 'name': 'Alpha'}
        same_day_a = {'reg_date': datetime.date(2021, 1, 1), 'name': 'Beta'}
        self.assertLess(sort_key(newer), sort_key(older))
        self.assertLess(sort_key(newer), sort_key(same_day_a))

    def test_exported_order(self):
        items = [
            {'name': 'Zebra', 'reg_date': datetime.date(2019, 5, 1)},
            {'name': 'Beta', 'reg_date': datetime.date(2021, 1, 1)},
            {'name': 'Alpha', 'reg_date': datetime.date(2021, 1, 1)},
            {'name': 'Omega', 'reg_date': datetime.date(2022, 6, 30)},
        ]
        exported = self.export(items)
        self.assertEqual(
            [item['name'] for item in exported],
            ['Omega', 'Alpha', 'Beta', 'Zebra'],
        )

    def test_partial_marker_survives_export(self):
        items = [
            {'name': 'Ok', 'reg_date': datetime.date(2021, 1, 1),
             'type': 'party', 'is_active': True},
            {'name': 'Degraded', 'reg_date': datetime.date(2021, 1, 2),
             'type': None, 'is_active': None, 'partial': True},
        ]
        exported = self.export(items)
        self.assertIs(exported[0]['partial'], True)
        self.assertIsNone(exported[0]['type'])
        self.assertNotIn('partial', exported[1])


if __name__ == '__main__':
    unittest.main()
