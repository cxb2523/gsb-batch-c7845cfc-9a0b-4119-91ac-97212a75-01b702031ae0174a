# -*- coding: utf-8 -*-
"""Offline tests for the label-based place metadata parsing in googlemaps.py.

These tests feed saved Google Maps page HTML straight into
``parse_place_html``: no Chrome driver and no network access are required.
Run with:

    python -I -m pytest tests/
"""
import sys
from pathlib import Path

# make the repo root importable regardless of the working directory / -I mode
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bs4 import BeautifulSoup

from googlemaps import parse_place_html, GoogleMapsScraper

PLACE_URL = 'https://www.google.com/maps/place/Cafe+Roma/@41.89193,12.51133,17z/data=!4m6!3m5'

EXPECTED_FIELDS = [
    'name', 'overall_rating', 'n_reviews', 'n_photos', 'category',
    'description', 'address', 'website', 'phone_number', 'plus_code',
    'opening_hours', 'url', 'lat', 'long',
]


def info_row(item_id, text, tag='button', aria_label=None, href=None, extra_attrs=None):
    """Render one Google Maps information row (address/website/phone/...).

    The element carrying ``data-item-id`` is an outer wrapper containing the
    clickable control and the ``Io6YTe`` text value, matching the real page
    structure.
    """
    wrapper_attrs = []
    if item_id:
        wrapper_attrs.append(f'data-item-id="{item_id}"')
    if aria_label is not None:
        wrapper_attrs.append(f'aria-label="{aria_label}"')
    if extra_attrs:
        wrapper_attrs.extend(extra_attrs)
    wrapper_attr_str = ' '.join(wrapper_attrs)

    if tag == 'a' and href is not None:
        control = f'<a href="{href}"><span class="some-icon"></span></a>'
    else:
        control = f'<{tag}><span class="some-icon"></span></{tag}>'

    return (
        f'<div class="AeaXub" {wrapper_attr_str}>'
        f'{control}'
        f'<div class="Io6YTe fontBodyMedium">{text}</div>'
        f'</div>'
    )


def build_page(rows_html='', name='Cafe Roma', with_rating=True, with_photos=True,
               category='Coffee shop', description='A cosy cafe in the centre.',
               hours_aria=None, hours_oh=None, h1_class='DUwDvf fontHeadlineLarge',
               extra_head=''):
    rating_block = ''
    if with_rating:
        rating_block = (
            '<div class="F7nice ">'
            '<span class="ceNzKf" aria-label="Rated 4.5 out of 5"></span>'
            '<span>4.5</span><span>(1,234)</span>'
            '</div>'
        )
    photos_block = '<div class="YkuOqf">120 photos</div>' if with_photos else ''
    category_block = (
        f'<button class="DkEaL" jsaction="pane.rating.category">{category}</button>'
        if category is not None else ''
    )
    description_block = (
        f'<div class="PYvSYb">{description}</div>' if description is not None else ''
    )
    name_block = f'<h1 class="{h1_class}">{name}</h1>' if name is not None else ''

    hours_block = ''
    if hours_aria is not None:
        hours_block = f'<div class="t39EBf GUrTXd" aria-label="{hours_aria}"></div>'
    if hours_oh is not None:
        hours_block += (
            f'<button data-item-id="oh" aria-label="{hours_oh}">'
            f'<div class="Io6YTe fontBodyMedium">Open now</div></button>'
        )

    return f"""<!DOCTYPE html>
<html><body>
<div role="main">
  {extra_head}
  {name_block}
  {rating_block}
  {photos_block}
  {category_block}
  {description_block}
  <div class="fontBodyMedium">
    {rows_html}
  </div>
  {hours_block}
</div>
</body></html>"""


ALL_ROWS = ''.join([
    info_row('address:0x123', 'Via del Corso 1, Rome'),
    info_row('authority:https://cafe-roma.example', 'cafe-roma.example',
             tag='a', href='https://cafe-roma.example'),
    info_row('phone:tel:+390612345678', '+39 06 1234 5678'),
    info_row('oloc:PVX8+9H Rome', 'PVX8+9H Rome'),
])


def assert_complete_structure(place):
    """Every field must exist even when nothing could be parsed."""
    assert list(place.keys()) == EXPECTED_FIELDS


def test_full_page_all_fields_located_by_label():
    html = build_page(
        ALL_ROWS,
        hours_aria='Monday: 9:00 AM – 6:00 PM; Tuesday: 9:00 AM – 6:00 PM',
    )
    place = parse_place_html(html, PLACE_URL)

    assert_complete_structure(place)
    assert place['name'] == 'Cafe Roma'
    assert place['overall_rating'] == 4.5
    assert place['n_reviews'] == 1234
    assert place['n_photos'] == 120
    assert place['category'] == 'Coffee shop'
    assert place['description'] == 'A cosy cafe in the centre.'
    assert place['address'] == 'Via del Corso 1, Rome'
    # website row is a link: the actual URL is returned, not the display label
    assert place['website'] == 'https://cafe-roma.example'
    assert place['phone_number'] == '+39 06 1234 5678'
    assert place['plus_code'] == 'PVX8+9H Rome'
    assert place['opening_hours'] == (
        'Monday: 9:00 AM – 6:00 PM; Tuesday: 9:00 AM – 6:00 PM'
    )
    assert place['url'] == PLACE_URL
    assert place['lat'] == '41.89193'
    assert place['long'] == '12.51133'


def test_missing_website_does_not_shift_phone_and_plus_code():
    # The exact bug from the ticket: no website row. The old fixed-index code
    # wrote the phone number into the website field.
    rows = ''.join([
        info_row('address:0x123', 'Via del Corso 1, Rome'),
        info_row('phone:tel:+390612345678', '+39 06 1234 5678'),
        info_row('oloc:PVX8+9H Rome', 'PVX8+9H Rome'),
    ])
    place = parse_place_html(build_page(rows), PLACE_URL)

    assert place['website'] is None
    assert place['phone_number'] == '+39 06 1234 5678'
    assert place['plus_code'] == 'PVX8+9H Rome'
    assert place['address'] == 'Via del Corso 1, Rome'


def test_missing_address_does_not_shift_remaining_fields():
    rows = ''.join([
        info_row('authority:https://cafe-roma.example', 'cafe-roma.example',
                 tag='a', href='https://cafe-roma.example'),
        info_row('phone:tel:+390612345678', '+39 06 1234 5678'),
        info_row('oloc:PVX8+9H Rome', 'PVX8+9H Rome'),
    ])
    place = parse_place_html(build_page(rows), PLACE_URL)

    assert place['address'] is None
    assert place['website'] == 'https://cafe-roma.example'
    assert place['phone_number'] == '+39 06 1234 5678'
    assert place['plus_code'] == 'PVX8+9H Rome'


def test_missing_phone_only():
    rows = ''.join([
        info_row('address:0x123', 'Via del Corso 1, Rome'),
        info_row('authority:https://cafe-roma.example', 'cafe-roma.example',
                 tag='a', href='https://cafe-roma.example'),
        info_row('oloc:PVX8+9H Rome', 'PVX8+9H Rome'),
    ])
    place = parse_place_html(build_page(rows), PLACE_URL)

    assert place['phone_number'] is None
    assert place['address'] == 'Via del Corso 1, Rome'
    assert place['website'] == 'https://cafe-roma.example'
    assert place['plus_code'] == 'PVX8+9H Rome'


def test_only_address_present():
    rows = info_row('address:0x123', 'Via del Corso 1, Rome')
    place = parse_place_html(build_page(rows), PLACE_URL)

    assert place['address'] == 'Via del Corso 1, Rome'
    assert place['website'] is None
    assert place['phone_number'] is None
    assert place['plus_code'] is None


def test_field_order_on_page_is_irrelevant():
    # Rows in a completely different order than the old fixed-index assumption.
    rows = ''.join([
        info_row('oloc:PVX8+9H Rome', 'PVX8+9H Rome'),
        info_row('phone:tel:+390612345678', '+39 06 1234 5678'),
        info_row('address:0x123', 'Via del Corso 1, Rome'),
        info_row('authority:https://cafe-roma.example', 'cafe-roma.example',
                 tag='a', href='https://cafe-roma.example'),
    ])
    place = parse_place_html(build_page(rows), PLACE_URL)

    assert place['address'] == 'Via del Corso 1, Rome'
    assert place['website'] == 'https://cafe-roma.example'
    assert place['phone_number'] == '+39 06 1234 5678'
    assert place['plus_code'] == 'PVX8+9H Rome'


def test_missing_opening_hours_does_not_affect_other_fields():
    rows = ''.join([
        info_row('address:0x123', 'Via del Corso 1, Rome'),
        info_row('authority:https://cafe-roma.example', 'cafe-roma.example',
                 tag='a', href='https://cafe-roma.example'),
        info_row('phone:tel:+390612345678', '+39 06 1234 5678'),
    ])
    place = parse_place_html(build_page(rows), PLACE_URL)  # no hours block at all

    assert place['opening_hours'] is None
    assert place['address'] == 'Via del Corso 1, Rome'
    assert place['website'] == 'https://cafe-roma.example'
    assert place['phone_number'] == '+39 06 1234 5678'


def test_opening_hours_from_oh_data_item_id():
    rows = info_row('address:0x123', 'Via del Corso 1, Rome')
    html = build_page(rows, hours_oh='Monday: 9:00 AM – 6:00 PM')
    place = parse_place_html(html, PLACE_URL)

    assert place['opening_hours'] == 'Monday: 9:00 AM – 6:00 PM'
    assert place['address'] == 'Via del Corso 1, Rome'


def test_no_recognisable_place_name_returns_complete_dict():
    html = build_page(ALL_ROWS, name=None)
    place = parse_place_html(html, PLACE_URL)

    assert_complete_structure(place)
    assert place['name'] is None
    # other fields still parsed
    assert place['address'] == 'Via del Corso 1, Rome'
    assert place['phone_number'] == '+39 06 1234 5678'


def test_obfuscated_h1_class_still_finds_name():
    html = build_page(ALL_ROWS, h1_class='some-new-obfuscated-class')
    place = parse_place_html(html, PLACE_URL)

    assert place['name'] == 'Cafe Roma'


def test_empty_page_returns_complete_dict_with_none_values():
    place = parse_place_html('<html><body></body></html>', PLACE_URL)

    assert_complete_structure(place)
    assert place['name'] is None
    assert place['overall_rating'] is None
    assert place['n_reviews'] == 0
    assert place['n_photos'] == 0
    assert place['category'] is None
    assert place['description'] is None
    assert place['address'] is None
    assert place['website'] is None
    assert place['phone_number'] is None
    assert place['plus_code'] is None
    assert place['opening_hours'] is None
    assert place['lat'] == '41.89193'
    assert place['long'] == '12.51133'


def test_unparseable_url_yields_none_coordinates():
    place = parse_place_html(build_page(ALL_ROWS), 'not-a-valid-maps-url')

    assert place['url'] == 'not-a-valid-maps-url'
    assert place['lat'] is None
    assert place['long'] is None


def test_aria_label_fallback_when_data_item_id_absent():
    # Older/localised markup: only aria-label identifies the rows.
    rows = ''.join([
        info_row('', 'Via del Corso 1, Rome', aria_label='Address: Via del Corso 1, Rome'),
        info_row('', 'cafe-roma.example', tag='a',
                 href='https://cafe-roma.example',
                 aria_label='Website: cafe-roma.example'),
        info_row('', '+39 06 1234 5678', aria_label='Phone: +39 06 1234 5678'),
        info_row('', 'PVX8+9H Rome', aria_label='Plus code: PVX8+9H Rome'),
    ])
    place = parse_place_html(build_page(rows), PLACE_URL)

    assert place['address'] == 'Via del Corso 1, Rome'
    assert place['website'] == 'https://cafe-roma.example'
    assert place['phone_number'] == '+39 06 1234 5678'
    assert place['plus_code'] == 'PVX8+9H Rome'


def test_mixed_markup_data_id_and_aria_label_coexist():
    # Address has a data-item-id; phone only has an aria-label.
    rows = ''.join([
        info_row('address:0x123', 'Via del Corso 1, Rome'),
        info_row('', '+39 06 1234 5678', aria_label='Phone: +39 06 1234 5678'),
    ])
    place = parse_place_html(build_page(rows), PLACE_URL)

    assert place['address'] == 'Via del Corso 1, Rome'
    assert place['phone_number'] == '+39 06 1234 5678'
    assert place['website'] is None
    assert place['plus_code'] is None


def test_phone_value_from_phone_data_item_id_without_text_node():
    # Row only carries the number inside data-item-id (no Io6YTe text value).
    row = ('<button data-item-id="phone:tel:+390612345678">'
           '<span class="some-icon"></span></button>')
    place = parse_place_html(build_page(row), PLACE_URL)

    assert place['phone_number'] == 'tel:+390612345678'


def test_accepts_bytes_html():
    html = build_page(ALL_ROWS).encode('utf-8')
    place = parse_place_html(html, PLACE_URL)

    assert place['address'] == 'Via del Corso 1, Rome'
    assert place['phone_number'] == '+39 06 1234 5678'


def test_accepts_preparsed_beautifulsoup():
    soup = BeautifulSoup(build_page(ALL_ROWS), 'html.parser')
    place = parse_place_html(soup, PLACE_URL)

    assert place['name'] == 'Cafe Roma'
    assert place['address'] == 'Via del Corso 1, Rome'


def test_saved_html_file_can_be_parsed_offline(tmp_path):
    # Simulates the workflow described in the ticket: save a page to disk once,
    # then parse the file directly without Chrome.
    html_path = tmp_path / 'saved_place.html'
    html_path.write_text(build_page(ALL_ROWS), encoding='utf-8')

    place = parse_place_html(html_path.read_text(encoding='utf-8'), PLACE_URL)

    assert place['website'] == 'https://cafe-roma.example'
    assert place['plus_code'] == 'PVX8+9H Rome'


def test_private_parse_place_wrapper_still_works_with_soup():
    # The instance method is retained for backwards compatibility; name
    # mangling means it has to be accessed through the mangled name.
    scraper = GoogleMapsScraper.__new__(GoogleMapsScraper)  # no __init__: no Chrome
    soup = BeautifulSoup(build_page(ALL_ROWS), 'html.parser')
    place = scraper._GoogleMapsScraper__parse_place(soup, PLACE_URL)

    assert place['address'] == 'Via del Corso 1, Rome'
    assert place['phone_number'] == '+39 06 1234 5678'
    assert place['website'] == 'https://cafe-roma.example'


def test_whitespace_inside_rows_is_normalised():
    row = ('<button data-item-id="address:0x123">'
           '<div class="Io6YTe fontBodyMedium">  Via del\nCorso\t1,   Rome </div>'
           '</button>')
    place = parse_place_html(build_page(row), PLACE_URL)

    assert place['address'] == 'Via del Corso 1, Rome'


def test_unrelated_data_item_ids_are_ignored():
    # Rows such as menus, hotels or "review updates" must not be mistaken for
    # address/website/phone/plus-code.
    rows = ''.join([
        info_row('menu:https://example.com/menu', 'Menu'),
        info_row('hotel:0xabc', 'Hotel booking'),
        info_row('address:0x123', 'Via del Corso 1, Rome'),
        info_row('phone:tel:+390612345678', '+39 06 1234 5678'),
    ])
    place = parse_place_html(build_page(rows), PLACE_URL)

    assert place['address'] == 'Via del Corso 1, Rome'
    assert place['phone_number'] == '+39 06 1234 5678'
    assert place['website'] is None
    assert place['plus_code'] is None
