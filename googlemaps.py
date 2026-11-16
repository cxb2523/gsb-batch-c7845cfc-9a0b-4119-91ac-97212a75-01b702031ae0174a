# -*- coding: utf-8 -*-
import itertools
import logging
import re
import time
import traceback
from datetime import datetime

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver import ChromeOptions as Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

GM_WEBPAGE = 'https://www.google.com/maps/'
MAX_WAIT = 10
MAX_RETRY = 5
MAX_SCROLLS = 40

# ---------------------------------------------------------------------------
# Offline place metadata parsing
#
# Google Maps renders each piece of contact information (address, website,
# phone, ...) in its own row whose icon carries a stable, language-neutral
# ``data-item-id`` attribute. Matching on that attribute instead of a fixed
# position keeps every field correctly placed when one of the rows is absent
# from the page. The helpers below only need a saved page's HTML and work
# without a running browser / network connection.
# ---------------------------------------------------------------------------

# data-item-id prefix -> place dictionary field
_INFO_ID_PREFIXES = (
    ('address', 'address'),
    ('authority', 'website'),
    ('phone', 'phone_number'),
    ('oloc', 'plus_code'),
)

# fallback for older/localised pages that expose the label through aria-label
# instead of data-item-id
_INFO_ARIA_LABELS = (
    ('Address', 'address'),
    ('Website', 'website'),
    ('Phone', 'phone_number'),
    ('Plus code', 'plus_code'),
)


def _clean_info_text(text):
    """Collapse whitespace inside an information row."""
    return re.sub(r'\s+', ' ', text).strip()


def _row_value(row):
    """Extract the displayed value of an information row.

    The text value lives in an ``Io6YTe`` element; fall back to the row's own
    text for pages whose markup differs slightly.
    """
    value_el = row.find(class_='Io6YTe')
    text = value_el.get_text() if value_el is not None else row.get_text()
    text = _clean_info_text(text)
    return text or None


def _website_value(row):
    """Return the website value, preferring the link's target URL."""
    link = row.find('a', href=True)
    if link is not None:
        href = link['href'].strip()
        if href.startswith('http://') or href.startswith('https://'):
            return href
    return _row_value(row)


def _phone_value(row):
    """Return the phone value, accepting both the text and ``phone:`` id."""
    value = _row_value(row)
    if value is not None:
        return value
    item_id = row.get('data-item-id') or ''
    if item_id.startswith('phone:'):
        number = item_id[len('phone:'):].strip()
        return number or None
    return None


def _extract_info_rows(response):
    """Locate address/website/phone/plus-code rows by their actual labels.

    Each row is matched independently, so a missing row leaves the
    corresponding field empty instead of shifting the following fields.
    """
    fields = {
        'address': None,
        'website': None,
        'phone_number': None,
        'plus_code': None,
    }

    # primary strategy: stable data-item-id attribute
    for row in response.find_all(attrs={'data-item-id': True}):
        item_id = row.get('data-item-id') or ''
        for prefix, field in _INFO_ID_PREFIXES:
            if item_id == prefix or item_id.startswith(prefix + ':'):
                if fields[field] is None:
                    if field == 'website':
                        fields[field] = _website_value(row)
                    elif field == 'phone_number':
                        fields[field] = _phone_value(row)
                    else:
                        fields[field] = _row_value(row)
                break

    # fallback: aria-label based matching for pages (or individual rows)
    # without data-item-id; each field is filled independently
    for row in response.find_all(attrs={'aria-label': True}):
        if row.find(class_='Io6YTe') is None:
            continue
        aria_label = row.get('aria-label') or ''
        for label, field in _INFO_ARIA_LABELS:
            if fields[field] is None and (aria_label == label or aria_label.startswith(label + ':')):
                if field == 'website':
                    fields[field] = _website_value(row)
                elif field == 'phone_number':
                    fields[field] = _phone_value(row)
                else:
                    fields[field] = _row_value(row)
                break

    return fields


def _normalize_hours_text(text):
    """Normalise the special spacing characters Google uses in hours labels."""
    # narrow no-break space (U+202F) and thin space (U+2009) -> regular space
    return text.replace(' ', ' ').replace(' ', ' ')


def _extract_opening_hours(response):
    """Extract opening hours independently from the other metadata fields."""
    # markup used by the page version this scraper was built against
    hours_el = response.find('div', class_='t39EBf GUrTXd')
    if hours_el is not None and hours_el.get('aria-label'):
        return _normalize_hours_text(hours_el['aria-label'])

    # newer markup: the hours row carries an ``oh`` data-item-id
    for row in response.find_all(attrs={'data-item-id': True}):
        if (row.get('data-item-id') or '').split(':', 1)[0] == 'oh':
            aria_label = row.get('aria-label')
            if aria_label:
                return _normalize_hours_text(aria_label)
            value = _row_value(row)
            if value is not None:
                return value

    # last resort: any element explicitly labelled with opening hours
    hours_el = response.find(attrs={'aria-label': 'Opening hours'})
    if hours_el is not None:
        value = _row_value(hours_el)
        if value is not None:
            return value

    return None


def _find_by_class(response, name):
    """Find an element by class, tolerating trailing/leading whitespace.

    Google's obfuscated class names sometimes appear with a trailing space in
    the original markup (e.g. ``class="F7nice "``). BeautifulSoup tokenises
    class attributes, so an exact string lookup including the space would
    never match; search by the stripped token instead.
    """
    return response.find(class_=name.strip())


def parse_place_html(html, url=''):
    """Parse place metadata from a saved page's HTML.

    Args:
        html (str | bytes | BeautifulSoup): HTML of a Google Maps place page,
            as returned by ``driver.page_source`` or read from disk.
        url (str): place URL the HTML was captured from. Stored in the
            returned dictionary and used to derive the coordinates.

    Returns:
        dict: the place metadata with the same field names as
        ``GoogleMapsScraper.get_account``. Fields that cannot be located on
        the page are set to ``None``; the dictionary structure is always
        complete, even for a page without a recognisable place name.
    """
    if isinstance(html, BeautifulSoup):
        response = html
    else:
        response = BeautifulSoup(html, 'html.parser')

    place = {}

    try:
        place['name'] = response.find('h1', class_='DUwDvf fontHeadlineLarge').text.strip()
    except Exception:
        # fallback: any populated h1 (class names are obfuscated and may change)
        h1 = response.find('h1')
        name = _clean_info_text(h1.get_text()) if h1 is not None else ''
        place['name'] = name or None

    try:
        rating_container = _find_by_class(response, 'F7nice ')
        place['overall_rating'] = float(rating_container.find('span', class_='ceNzKf')['aria-label'].split(' ')[1])
    except Exception:
        place['overall_rating'] = None

    try:
        rating_container = _find_by_class(response, 'F7nice ')
        place['n_reviews'] = int(rating_container.text.split('(')[1].replace(',', '').replace(')', ''))
    except Exception:
        place['n_reviews'] = 0

    try:
        place['n_photos'] = int(response.find('div', class_='YkuOqf').text.replace('.', '').replace(',', '').split(' ')[0])
    except Exception:
        place['n_photos'] = 0

    try:
        place['category'] = response.find('button', jsaction='pane.rating.category').text.strip()
    except Exception:
        place['category'] = None

    try:
        place['description'] = response.find('div', class_='PYvSYb').text.strip()
    except Exception:
        place['description'] = None

    # label-based extraction: every field is located independently, so missing
    # rows never shift the remaining fields
    info_fields = _extract_info_rows(response)
    place['address'] = info_fields['address']
    place['website'] = info_fields['website']
    place['phone_number'] = info_fields['phone_number']
    place['plus_code'] = info_fields['plus_code']

    # opening hours are parsed in their own step and cannot affect the fields
    # above
    place['opening_hours'] = _extract_opening_hours(response)

    place['url'] = url

    try:
        lat, lng, _z = url.split('/')[6].split(',')
        place['lat'] = lat[1:]
        place['long'] = lng
    except Exception:
        place['lat'] = None
        place['long'] = None

    return place


class GoogleMapsScraper:

    def __init__(self, debug=False):
        self.debug = debug
        self.driver = self.__get_driver()
        self.logger = self.__get_logger()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        if exc_type is not None:
            traceback.print_exception(exc_type, exc_value, tb)

        self.driver.close()
        self.driver.quit()

        return True

    def sort_by(self, url, ind):

        self.driver.get(url)
        self.__click_on_cookie_agreement()

        wait = WebDriverWait(self.driver, MAX_WAIT)

        # open dropdown menu
        clicked = False
        tries = 0
        while not clicked and tries < MAX_RETRY:
            try:
                menu_bt = wait.until(EC.element_to_be_clickable((By.XPATH, '//button[@data-value=\'Sort\']')))
                menu_bt.click()

                clicked = True
                time.sleep(3)
            except Exception as e:
                tries += 1
                self.logger.warn('Failed to click sorting button')

            # failed to open the dropdown
            if tries == MAX_RETRY:
                return -1

        #  element of the list specified according to ind
        recent_rating_bt = self.driver.find_elements(By.XPATH, '//div[@role=\'menuitemradio\']')[ind]
        recent_rating_bt.click()

        # wait to load review (ajax call)
        time.sleep(5)

        return 0

    def get_places(self, keyword_list=None):

        df_places = pd.DataFrame()
        search_point_url_list = self._gen_search_points_from_square(keyword_list=keyword_list)

        for i, search_point_url in enumerate(search_point_url_list):
            print(search_point_url)

            if (i+1) % 10 == 0:
                print(f"{i}/{len(search_point_url_list)}")
                df_places = df_places[['search_point_url', 'href', 'name', 'rating', 'num_reviews', 'close_time', 'other']]
                df_places.to_csv('output/places_wax.csv', index=False)


            try:
                self.driver.get(search_point_url)
            except NoSuchElementException:
                self.driver.quit()
                self.driver = self.__get_driver()
                self.driver.get(search_point_url)

            # scroll to load all (20) places into the page
            scrollable_div = self.driver.find_element(By.CSS_SELECTOR,
                "div.m6QErb.DxyBCb.kA9KIf.dS8AEf.ecceSd > div[aria-label*='Results for']")
            for i in range(10):
                self.driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight', scrollable_div)

            # Get places names and href
            time.sleep(2)
            response = BeautifulSoup(self.driver.page_source, 'html.parser')
            div_places = response.select('div[jsaction] > a[href]')

            for div_place in div_places:
                place_info = {
                    'search_point_url': search_point_url.replace('https://www.google.com/maps/search/', ''),
                    'href': div_place['href'],
                    'name': div_place['aria-label']
                }

                df_places = df_places.append(place_info, ignore_index=True)

            # TODO: implement click to handle > 20 places

        df_places = df_places[['search_point_url', 'href', 'name']]
        df_places.to_csv('output/places_wax.csv', index=False)



    def get_reviews(self, offset):

        # scroll to load reviews
        self.__scroll()

        # wait for other reviews to load (ajax)
        time.sleep(4)

        # expand review text
        self.__expand_reviews()

        # parse reviews
        response = BeautifulSoup(self.driver.page_source, 'html.parser')
        # TODO: Subject to changes
        rblock = response.find_all('div', class_='jftiEf fontBodyMedium')
        parsed_reviews = []
        for index, review in enumerate(rblock):
            if index >= offset:
                r = self.__parse(review)
                parsed_reviews.append(r)

                # logging to std out
                print(r)

        return parsed_reviews



    # need to use different url wrt reviews one to have all info
    def get_account(self, url):

        self.driver.get(url)
        self.__click_on_cookie_agreement()

        # ajax call also for this section
        time.sleep(2)

        resp = BeautifulSoup(self.driver.page_source, 'html.parser')

        place_data = parse_place_html(resp, url)

        return place_data


    def __parse(self, review):

        item = {}

        try:
            # TODO: Subject to changes
            id_review = review['data-review-id']
        except Exception as e:
            id_review = None

        try:
            # TODO: Subject to changes
            username = review['aria-label']
        except Exception as e:
            username = None

        try:
            # TODO: Subject to changes
            review_text = self.__filter_string(review.find('span', class_='wiI7pd').text)
        except Exception as e:
            review_text = None

        try:
            # TODO: Subject to changes
            rating = float(review.find('span', class_='kvMYJc')['aria-label'].split(' ')[0])
        except Exception as e:
            rating = None

        try:
            # TODO: Subject to changes
            relative_date = review.find('span', class_='rsqaWe').text
        except Exception as e:
            relative_date = None

        try:
            n_reviews = review.find('div', class_='RfnDt').text.split(' ')[3]
        except Exception as e:
            n_reviews = 0

        try:
            user_url = review.find('button', class_='WEBjve')['data-href']
        except Exception as e:
            user_url = None

        item['id_review'] = id_review
        item['caption'] = review_text

        # depends on language, which depends on geolocation defined by Google Maps
        # custom mapping to transform into date should be implemented
        item['relative_date'] = relative_date

        # store datetime of scraping and apply further processing to calculate
        # correct date as retrieval_date - time(relative_date)
        item['retrieval_date'] = datetime.now()
        item['rating'] = rating
        item['username'] = username
        item['n_review_user'] = n_reviews
        #item['n_photo_user'] = n_photos  ## not available anymore
        item['url_user'] = user_url

        return item


    def __parse_place(self, response, url):
        # Kept for backwards compatibility with any external caller. The actual
        # label-based parsing lives in the module-level parse_place_html so it
        # can be exercised offline against saved page HTML.
        return parse_place_html(response, url)


    def _gen_search_points_from_square(self, keyword_list=None):
        # TODO: Generate search points from corners of square

        keyword_list = [] if keyword_list is None else keyword_list

        square_points = pd.read_csv('input/square_points.csv')

        cities = square_points['city'].unique()

        search_urls = []

        for city in cities:

            df_aux = square_points[square_points['city'] == city]
            latitudes = df_aux['latitude'].unique()
            longitudes = df_aux['longitude'].unique()
            coordinates_list = list(itertools.product(latitudes, longitudes, keyword_list))

            search_urls += [f"https://www.google.com/maps/search/{coordinates[2]}/@{str(coordinates[1])},{str(coordinates[0])},{str(15)}z"
             for coordinates in coordinates_list]

        return search_urls


    # expand review description
    def __expand_reviews(self):
        # use XPath to load complete reviews
        # TODO: Subject to changes
        buttons = self.driver.find_elements(By.CSS_SELECTOR,'button.w8nwRe.kyuRq')
        for button in buttons:
            self.driver.execute_script("arguments[0].click();", button)


    def __scroll(self):
        # TODO: Subject to changes
        scrollable_div = self.driver.find_element(By.CSS_SELECTOR,'div.m6QErb.DxyBCb.kA9KIf.dS8AEf')
        self.driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight', scrollable_div)
        #self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")


    def __get_logger(self):
        # create logger
        logger = logging.getLogger('googlemaps-scraper')
        logger.setLevel(logging.DEBUG)

        # create console handler and set level to debug
        fh = logging.FileHandler('gm-scraper.log')
        fh.setLevel(logging.DEBUG)

        # create formatter
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # add formatter to ch
        fh.setFormatter(formatter)

        # add ch to logger
        logger.addHandler(fh)

        return logger


    def __get_driver(self, debug=False):
        options = Options()

        if not self.debug:
            options.add_argument("--headless")
        else:
            options.add_argument("--window-size=1366,768")

        options.add_argument("--disable-notifications")
        #options.add_argument("--lang=en-GB")
        options.add_argument("--accept-lang=en-GB")
        input_driver = webdriver.Chrome(service=Service(), options=options)

         # click on google agree button so we can continue (not needed anymore)
         # EC.element_to_be_clickable((By.XPATH, '//span[contains(text(), "I agree")]')))
        input_driver.get(GM_WEBPAGE)

        return input_driver

    # cookies agreement click
    def __click_on_cookie_agreement(self):
        try:
            agree = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, '//span[contains(text(), "Reject all")]')))
            agree.click()

            # back to the main page
            # self.driver.switch_to_default_content()

            return True
        except:
            return False

    # util function to clean special characters
    def __filter_string(self, str):
        strOut = str.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
        return strOut
