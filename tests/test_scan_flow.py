#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Local, dependency-free tests for the TorSpider scan flow.

Run from the repository root with:

    python -m unittest discover -s tests -v

The tests use fake response objects and a fake session, so no Tor proxy or
backend API is required. They cover the four behaviours the crawl loop must
guarantee:

1. A redirect to a valid .onion address is recorded *and* the target page is
   still parsed.
2. A redirect to a non-onion address is recorded but never fetched.
3. An unknown content type is parsed as text and marked as unknown.
4. An unknown content type that cannot be parsed produces a readable fault.

In every case exactly one scan_result is shipped to the backend.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from libs.classes import SpiderURL  # noqa: E402
from TorSpider import Spider, is_valid_onion_url  # noqa: E402


PAGE_HTML = (
    '<html><head><title>Example Onion</title></head><body>'
    '<a href="http://ccc.onion/other">onion link</a>'
    '<a href="http://example.com/clearnet">clearnet link</a>'
    '<form action="/submit" method="post">'
    '<input type="text" name="q" value="v">'
    '</form>'
    '</body></html>'
)

SOURCE_URL = 'http://aaa.onion/page'
TARGET_URL = 'http://bbb.onion/target'


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, headers=None, text='',
                 content=None, text_error=None):
        self.status_code = status_code
        self.headers = headers if headers is not None else {}
        self._text = text
        self._content = (content if content is not None
                         else text.encode('utf-8'))
        self._text_error = text_error

    @property
    def text(self):
        if self._text_error is not None:
            raise self._text_error
        return self._text

    @property
    def content(self):
        return self._content


class FakeSession:
    """Records requests and returns pre-built fake responses.

    ``responses`` maps a url to the response returned by ``get()``. ``head``
    returns ``head_response`` for every url unless ``head_responses`` maps a
    url to a specific response.
    """

    def __init__(self, head_response, get_responses=None, head_responses=None):
        self.head_response = head_response
        self.get_responses = get_responses or {}
        self.head_responses = head_responses or {}
        self.head_calls = []
        self.get_calls = []
        self.get_redirect_flags = []

    def head(self, url, timeout=None, allow_redirects=False):
        self.head_calls.append(url)
        return self.head_responses.get(url, self.head_response)

    def get(self, url, timeout=None, allow_redirects=True):
        self.get_calls.append(url)
        self.get_redirect_flags.append(allow_redirects)
        return self.get_responses[url]


class RecordingPoster:
    """Collects every payload handed to the backend."""

    def __init__(self):
        self.payloads = []

    def __call__(self, data):
        self.payloads.append(json.loads(data))
        return {'ok': True}

    def only(self, testcase):
        testcase.assertEqual(
            len(self.payloads), 1,
            'expected exactly one scan_result, got {}'.format(
                len(self.payloads)))
        return self.payloads[0]


def make_spider(session, poster):
    return Spider(session=session, poster=poster,
                  node_name_override='TestNode')


class RedirectSuccessTests(unittest.TestCase):
    def test_redirect_is_recorded_and_target_page_is_parsed(self):
        head = FakeResponse(status_code=302, headers={'location': TARGET_URL})
        target = FakeResponse(
            status_code=200,
            headers={'Content-Type': 'text/html; charset=utf-8'},
            text=PAGE_HTML)
        session = FakeSession(head, get_responses={TARGET_URL: target})
        poster = RecordingPoster()
        spider = make_spider(session, poster)

        result = spider.scan_and_report(SOURCE_URL, '')

        # Exactly one scan_result goes back to the backend.
        payload = poster.only(self)

        # The redirect target is preserved, and the target page was fetched.
        self.assertEqual(result.redirect, TARGET_URL)
        self.assertEqual(result.redirect_status, 302)
        self.assertEqual(session.get_calls, [TARGET_URL])
        # Redirects are handled explicitly so a non-onion hop is never
        # followed implicitly.
        self.assertEqual(session.get_redirect_flags, [False])

        # Page content that used to be lost is now present.
        self.assertEqual(result.title, 'Example Onion')
        self.assertTrue(result.online)
        self.assertIsNotNone(result.hash)
        self.assertEqual(result.scan_stage, 'complete')
        self.assertIsNone(result.fault)
        self.assertEqual(result.content_type, 'text')

        # Both the redirect target and onion links are reported; the clearnet
        # link is filtered out.
        self.assertIn(TARGET_URL, payload['new_urls'])
        self.assertIn('http://ccc.onion/other', payload['new_urls'])
        self.assertNotIn('http://example.com/clearnet', payload['new_urls'])

        # Forms were parsed from the followed page.
        self.assertEqual(len(payload['form_dicts']), 1)
        self.assertEqual(payload['form_dicts'][0]['text_fields'], {'q': 'v'})

        # The original JSON keys are all still present.
        for key in ('new_urls', 'online', 'url', 'scan_date', 'last_node',
                    'fault', 'title', 'form_dicts', 'hash'):
            self.assertIn(key, payload)
        self.assertEqual(payload['url'], SOURCE_URL)
        self.assertEqual(payload['last_node'], 'TestNode')


class RedirectToNonOnionTests(unittest.TestCase):
    def test_non_onion_redirect_is_recorded_but_not_fetched(self):
        head = FakeResponse(
            status_code=301,
            headers={'location': 'http://example.com/elsewhere'})
        session = FakeSession(head, get_responses={})
        poster = RecordingPoster()
        spider = make_spider(session, poster)

        result = spider.scan_and_report(SOURCE_URL, '')
        payload = poster.only(self)

        # The redirect target is kept for the backend to inspect.
        self.assertEqual(result.redirect, 'http://example.com/elsewhere')
        # ...but it must never be fetched.
        self.assertEqual(session.get_calls, [])
        self.assertEqual(session.head_calls, [SOURCE_URL])

        # The reason for stopping is recorded in a readable form. 'fault'
        # keeps its original meaning (the redirect status code).
        self.assertEqual(result.fault, '301')
        self.assertIn('non-onion', result.redirect_fault)
        self.assertIn('non-onion', result.fault_reason)
        self.assertEqual(result.scan_stage, 'redirect')
        self.assertIsNone(result.title)
        self.assertIsNone(result.hash)
        # No content was parsed, so no links or forms are reported.
        self.assertEqual(payload['new_urls'], [])
        self.assertEqual(payload['form_dicts'], [])


class UnknownContentTypeTests(unittest.TestCase):
    def test_unknown_type_is_parsed_as_text_and_marked(self):
        head = FakeResponse(status_code=200, headers={})
        page = FakeResponse(status_code=200, headers={}, text=PAGE_HTML)
        session = FakeSession(head, get_responses={SOURCE_URL: page})
        poster = RecordingPoster()
        spider = make_spider(session, poster)

        result = spider.scan_and_report(SOURCE_URL, '')
        payload = poster.only(self)

        # Parsing was attempted instead of dropping the page.
        self.assertEqual(session.get_calls, [SOURCE_URL])
        self.assertEqual(result.title, 'Example Onion')
        self.assertTrue(result.online)
        self.assertEqual(result.scan_stage, 'complete')
        self.assertIsNone(result.fault)

        # The unknown type is marked for the backend.
        self.assertEqual(result.content_type, 'unknown')
        self.assertTrue(result.type_unknown)
        self.assertEqual(payload['content_type'], 'unknown')
        self.assertTrue(payload['type_unknown'])
        self.assertIn('http://ccc.onion/other', payload['new_urls'])

    def test_unknown_type_and_unparseable_page_reports_readable_fault(self):
        head = FakeResponse(status_code=200, headers={})
        page = FakeResponse(
            status_code=200, headers={},
            text_error=UnicodeDecodeError('utf-8', b'\xff', 0, 1,
                                          'invalid start byte'))
        session = FakeSession(head, get_responses={SOURCE_URL: page})
        poster = RecordingPoster()
        spider = make_spider(session, poster)

        # No unhandled exception escapes.
        result = spider.scan_and_report(SOURCE_URL, '')
        payload = poster.only(self)

        self.assertEqual(session.get_calls, [SOURCE_URL])
        self.assertEqual(result.content_type, 'unknown')
        self.assertTrue(result.type_unknown)

        # The parse failure is reported as a readable fault.
        self.assertEqual(result.fault, 'parse error')
        self.assertIn('could not parse page as text', result.fault_reason)
        self.assertIn('UnicodeDecodeError', result.fault_reason)
        self.assertEqual(result.scan_stage, 'parse')
        self.assertEqual(payload['fault'], 'parse error')
        self.assertEqual(payload['scan_stage'], 'parse')
        self.assertIsNone(result.hash)


class OnionValidationTests(unittest.TestCase):
    def test_validator(self):
        self.assertTrue(is_valid_onion_url('http://aaa.onion/x'))
        self.assertTrue(is_valid_onion_url('https://aaa.onion'))
        self.assertFalse(is_valid_onion_url('http://aaa.onion.to/x'))
        self.assertFalse(is_valid_onion_url('http://example.com/'))
        self.assertFalse(is_valid_onion_url(''))
        self.assertFalse(is_valid_onion_url('ftp://aaa.onion/x'))


class AdditionalBranchTests(unittest.TestCase):
    """Every remaining branch must still report exactly one result."""

    def _run_with_head(self, status_code, headers=None, get_responses=None):
        head = FakeResponse(status_code=status_code, headers=headers or {})
        session = FakeSession(head, get_responses=get_responses or {})
        poster = RecordingPoster()
        spider = make_spider(session, poster)
        result = spider.scan_and_report(SOURCE_URL, '')
        return result, poster.only(self), session

    def test_fault_code_reports_once(self):
        result, payload, session = self._run_with_head(404)
        self.assertEqual(result.fault, '404')
        self.assertEqual(result.scan_stage, 'head')
        self.assertEqual(session.get_calls, [])
        self.assertTrue(payload['fault_reason'])

    def test_temporary_status_reports_once_without_fault(self):
        result, payload, session = self._run_with_head(503)
        self.assertIsNone(result.fault)
        self.assertEqual(result.scan_stage, 'head')
        self.assertTrue(payload['fault_reason'])

    def test_unknown_status_reports_once(self):
        result, payload, session = self._run_with_head(418)
        self.assertEqual(result.fault, '418')
        self.assertEqual(session.get_calls, [])

    def test_redirect_without_location_reports_once(self):
        result, payload, session = self._run_with_head(302)
        self.assertEqual(result.scan_stage, 'redirect')
        self.assertEqual(result.fault, '302')
        self.assertIn('Location', result.fault_reason)
        self.assertIsNone(result.redirect)
        self.assertEqual(session.get_calls, [])

    def test_non_text_head_type_is_not_fetched(self):
        result, payload, session = self._run_with_head(
            200, headers={'Content-Type': 'image/png'})
        self.assertEqual(result.fault, 'type: image')
        self.assertEqual(session.get_calls, [])

    def test_non_text_get_type_is_not_parsed(self):
        page = FakeResponse(status_code=200,
                            headers={'Content-Type': 'application/pdf'},
                            text='%PDF')
        result, payload, session = self._run_with_head(
            200, get_responses={SOURCE_URL: page})
        self.assertEqual(result.fault, 'type: application')
        self.assertEqual(result.scan_stage, 'content_type')

    def test_unchanged_page_reports_once(self):
        page = FakeResponse(
            status_code=200,
            headers={'Content-Type': 'text/html'},
            text=PAGE_HTML)
        result, payload, session = self._run_with_head(
            200,
            headers={'Content-Type': 'text/html'},
            get_responses={SOURCE_URL: page})
        # First scan establishes the hash.
        first_hash = result.hash
        self.assertIsNotNone(first_hash)

        session = FakeSession(
            FakeResponse(status_code=200,
                         headers={'Content-Type': 'text/html'}),
            get_responses={SOURCE_URL: page})
        poster = RecordingPoster()
        spider = make_spider(session, poster)
        result = spider.scan_and_report(SOURCE_URL, first_hash)
        payload = poster.only(self)
        self.assertEqual(result.scan_stage, 'unchanged')
        # The title is still reported even when the page did not change.
        self.assertEqual(payload['title'], 'Example Onion')

    def test_connection_error_reports_once(self):
        class BrokenSession:
            def __init__(self):
                self.get_calls = []
                self.head_calls = []

            def head(self, url, timeout=None, allow_redirects=False):
                self.head_calls.append(url)
                import requests
                raise requests.exceptions.ConnectionError('refused')

            def get(self, url, timeout=None, allow_redirects=True):
                self.get_calls.append(url)
                return FakeResponse()

        poster = RecordingPoster()
        spider = make_spider(BrokenSession(), poster)
        result = spider.scan_and_report(SOURCE_URL, '')
        payload = poster.only(self)
        self.assertFalse(payload['online'])
        self.assertIn('connection error', result.fault_reason)

    def test_unexpected_exception_still_reports_once(self):
        class ExplodingSession:
            def __init__(self):
                self.get_calls = []
                self.head_calls = []

            def head(self, url, timeout=None, allow_redirects=False):
                raise RuntimeError('boom')

            def get(self, url, timeout=None, allow_redirects=True):
                return FakeResponse()

        poster = RecordingPoster()
        spider = make_spider(ExplodingSession(), poster)
        result = spider.scan_and_report(SOURCE_URL, '')
        payload = poster.only(self)
        self.assertEqual(result.fault, 'unexpected error')
        self.assertIn('RuntimeError', payload['fault_reason'])

    def test_redirect_chain_to_onion_is_followed(self):
        hop = FakeResponse(302, headers={'location': 'http://ccc.onion/second'})
        final = FakeResponse(status_code=200,
                            headers={'Content-Type': 'text/html'},
                            text=PAGE_HTML)
        session = FakeSession(
            FakeResponse(status_code=200,
                         headers={'Content-Type': 'text/html'}),
            get_responses={SOURCE_URL: hop,
                           'http://ccc.onion/second': final})
        poster = RecordingPoster()
        spider = make_spider(session, poster)

        result = spider.scan_and_report(SOURCE_URL, '')
        poster.only(self)

        self.assertEqual(session.get_calls,
                         [SOURCE_URL, 'http://ccc.onion/second'])
        # Every hop is explicit, so nothing is followed behind our back.
        self.assertEqual(session.get_redirect_flags, [False, False])
        self.assertEqual(result.title, 'Example Onion')
        self.assertEqual(result.scan_stage, 'complete')
        self.assertIn('http://ccc.onion/second', result.new_urls)

    def test_redirect_target_fault_status_is_reported(self):
        head = FakeResponse(status_code=302, headers={'location': TARGET_URL})
        # The followed target itself is a 404 page.
        target = FakeResponse(status_code=404, headers={}, text='not found')
        session = FakeSession(head, get_responses={TARGET_URL: target})
        poster = RecordingPoster()
        spider = make_spider(session, poster)

        result = spider.scan_and_report(SOURCE_URL, '')
        payload = poster.only(self)

        self.assertEqual(session.get_calls, [TARGET_URL])
        self.assertEqual(result.redirect, TARGET_URL)
        self.assertFalse(result.online)
        self.assertEqual(result.fault, '404')
        self.assertEqual(result.scan_stage, 'head')
        self.assertIsNone(result.title)
        self.assertIsNone(payload['hash'])

    def test_body_redirect_to_non_onion_is_not_fetched(self):
        head = FakeResponse(status_code=200,
                            headers={'Content-Type': 'text/html'})
        hop = FakeResponse(302, headers={'location': 'http://example.com/evil'})
        session = FakeSession(head, get_responses={SOURCE_URL: hop})
        poster = RecordingPoster()
        spider = make_spider(session, poster)

        result = spider.scan_and_report(SOURCE_URL, '')
        payload = poster.only(self)

        # Only the original url was requested; the non-onion hop was not.
        self.assertEqual(session.get_calls, [SOURCE_URL])
        self.assertEqual(result.redirect, 'http://example.com/evil')
        self.assertEqual(result.scan_stage, 'redirect')
        self.assertIn('non-onion', payload['fault_reason'])
        self.assertIsNone(result.title)

    def test_crawl_loop_reports_one_result_per_url_and_stops_on_sleep(self):
        head = FakeResponse(
            status_code=301,
            headers={'location': 'http://example.com/elsewhere'})
        session = FakeSession(head, get_responses={})
        poster = RecordingPoster()
        spider = make_spider(session, poster)

        # The first 'next' query returns a url, the second one asks the loop
        # to stop by raising, which mirrors creating the 'sleep' file.
        queries = [{'url': SOURCE_URL, 'hash': ''}]

        def fake_get_query(endpoint, query):
            if queries:
                return queries.pop(0)
            raise KeyboardInterrupt('stop the loop')

        with mock.patch.object(spider, '_Spider__get_query',
                               side_effect=fake_get_query):
            with self.assertRaises(KeyboardInterrupt):
                spider.crawl()

        self.assertEqual(len(poster.payloads), 1)
        self.assertEqual(poster.payloads[0]['url'], SOURCE_URL)


class BackwardCompatibilityTests(unittest.TestCase):
    def test_new_fields_are_appended_to_json(self):
        original = {'new_urls', 'online', 'url', 'scan_date', 'last_node',
                    'fault', 'title', 'form_dicts', 'hash'}
        payload = json.loads(SpiderURL().to_json())
        # Every original key is still there...
        self.assertTrue(original.issubset(payload.keys()))
        # ...and the new keys are additive only.
        for key in ('redirect', 'redirect_fault', 'redirect_status',
                    'content_type', 'type_unknown', 'status_code',
                    'scan_stage', 'fault_reason'):
            self.assertIn(key, payload)
        self.assertGreater(len(payload), len(original))


if __name__ == '__main__':
    unittest.main(verbosity=2)
