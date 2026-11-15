# -*- coding:utf-8 -*-
reload(__import__('sys')).setdefaultencoding('utf-8')
__author__ = 'zcy'

"""Tests for the dynamic fetch path.

The real Selenium browser is replaced by a locally controllable fake driver so
that success, page-load timeout and browser-action failure can all be exercised
deterministically. The tests focus on:

* the capacity-1 driver semaphore always being returned (and never twice);
* selenium timeouts being converted to the project's TimeoutException while
  other exceptions propagate unchanged, so RetryMiddleware keeps working;
* no half-baked cookies being written back to the request's cookiejar and no
  browser cookies surviving a failed request;
* a static request still completing right after a failed dynamic request.
"""

import unittest
import cookielib

import gevent
from gevent.lock import BoundedSemaphore
from selenium.common.exceptions import TimeoutException as SeleniumTimeoutException

from pycreeper import downloader as downloader_pkg
from pycreeper.downloader import DownloadHandler
from pycreeper.spider import Spider
from pycreeper.http.request import Request
from pycreeper.http.response import Response
from pycreeper.utils.exceptions import TimeoutException
from pycreeper.utils import _get_cookies_from_cookiejar


class FakeDriver(object):
    """A controllable stand-in for a Selenium webdriver."""

    def __init__(self, get_error=None, cookies=None):
        self.get_error = get_error
        self._cookies = list(cookies or [])
        self.calls = []
        self.page_load_timeout = None
        self.current_url = 'http://example.com/after'
        self.page_source = '<html>ok</html>'
        self.delete_count = 0

    def set_page_load_timeout(self, timeout):
        self.page_load_timeout = timeout

    def get(self, url):
        self.calls.append(('get', url))
        if self.get_error is not None:
            raise self.get_error

    def execute_script(self, script):
        self.calls.append(('execute_script', script))

    def get_cookies(self):
        self.calls.append(('get_cookies',))
        # return copies so callers can never mutate the driver's own state
        return [dict(cookie) for cookie in self._cookies]

    def delete_all_cookies(self):
        self.calls.append(('delete_all_cookies',))
        self.delete_count += 1
        self._cookies = []

    def close(self):
        self.calls.append(('close',))


class BrowserActionError(Exception):
    """Raised deliberately by a browser action in the tests."""


def _failing_browser_action(driver):
    raise BrowserActionError('browser action blew up')


def _make_jar_cookie():
    return cookielib.Cookie(
        version=0, name='sid', value='abc',
        port=None, port_specified=False,
        domain='example.com', domain_specified=True, domain_initial_dot=False,
        path='/', path_specified=True, secure=False, expires=None,
        discard=False, comment=None, comment_url=None, rest={})


class _FakeHTTPResponse(object):
    def __init__(self):
        self.url = 'http://example.com/static'
        self.status_code = 200
        self.cookies = cookielib.CookieJar()
        self.content = 'static body'


class _FakeSession(object):
    def __init__(self):
        self.cookies = None

    def get(self, url, **kwargs):
        return _FakeHTTPResponse()

    def post(self, url, **kwargs):
        return _FakeHTTPResponse()


class _FakeRequests(object):
    Session = _FakeSession


class DynamicFetchSemaphoreTest(unittest.TestCase):
    def setUp(self):
        self.spider = Spider()
        self.spider.settings.set('TIMEOUT', 15)
        self.driver_sem = BoundedSemaphore(1)

    def assertSemaphoreAvailable(self, sem):
        """A released capacity-1 semaphore must be acquirable once more."""
        self.assertTrue(sem.acquire(blocking=False),
                        'driver semaphore was leaked and cannot be acquired')
        sem.release()

    def assertDriverCookiesCleared(self, driver):
        self.assertIn(('delete_all_cookies',), driver.calls,
                      'failed request must clear browser cookies')
        self.assertEqual(driver._cookies, [],
                         'browser cookies from the previous request remain')

    def test_dynamic_fetch_success_releases_semaphore_and_clears_cookies(self):
        driver = FakeDriver(cookies=[
            {'name': 'key', 'value': 'val',
             'domain': 'example.com', 'path': '/'},
        ])
        dh = DownloadHandler(self.spider, driver, self.driver_sem)
        request = Request('http://example.com/', dynamic=True, wait=0)

        response = dh.fetch(request)

        self.assertIsInstance(response, Response)
        self.assertEqual(response.body, '<html>ok</html>')
        jar_cookies = _get_cookies_from_cookiejar(response.cookiejar)
        self.assertEqual(len(jar_cookies), 1)
        self.assertEqual(jar_cookies[0].name, 'key')
        self.assertSemaphoreAvailable(self.driver_sem)
        self.assertDriverCookiesCleared(driver)

        # a second dynamic request must work: permit was returned and the
        # browser carries no cookies from the previous request
        response = dh.fetch(
            Request('http://example.com/again', dynamic=True, wait=0))
        self.assertIsInstance(response, Response)
        self.assertEqual(
            _get_cookies_from_cookiejar(response.cookiejar), [])
        self.assertSemaphoreAvailable(self.driver_sem)

    def test_dynamic_fetch_success_with_request_cookiejar(self):
        driver = FakeDriver(cookies=[
            {'name': 'key', 'value': 'val',
             'domain': 'example.com', 'path': '/'},
        ])
        dh = DownloadHandler(self.spider, driver, self.driver_sem)
        jar = cookielib.CookieJar()
        jar.set_cookie(_make_jar_cookie())
        request = Request('http://example.com/', dynamic=True,
                          wait=0, cookiejar=jar)

        response = dh.fetch(request)

        self.assertIsInstance(response, Response)
        # the request's own cookies were loaded into the browser first
        self.assertIn(('execute_script',),
                      [call[:1] for call in driver.calls])
        self.assertSemaphoreAvailable(self.driver_sem)
        self.assertDriverCookiesCleared(driver)

    def test_dynamic_fetch_page_load_timeout(self):
        driver = FakeDriver(
            get_error=SeleniumTimeoutException('page load timed out'))
        dh = DownloadHandler(self.spider, driver, self.driver_sem)
        jar = cookielib.CookieJar()
        jar.set_cookie(_make_jar_cookie())
        request = Request('http://example.com/slow', dynamic=True,
                          wait=0, cookiejar=jar)

        before = _get_cookies_from_cookiejar(request.cookiejar)
        self.assertRaises(TimeoutException, dh.fetch, request)
        after = _get_cookies_from_cookiejar(request.cookiejar)

        # selenium timeout is converted, semaphore returned, browser cleaned
        self.assertSemaphoreAvailable(self.driver_sem)
        self.assertDriverCookiesCleared(driver)
        # no half-baked cookies written back to the request's cookiejar
        self.assertEqual(before, after)
        self.assertEqual(len(after), 1)
        # cookies are only harvested after a successful page load
        self.assertNotIn(('get_cookies',), driver.calls)

    def test_dynamic_fetch_browser_action_exception(self):
        driver = FakeDriver(cookies=[
            {'name': 'stale', 'value': '1',
             'domain': 'example.com', 'path': '/'},
        ])
        dh = DownloadHandler(self.spider, driver, self.driver_sem)
        jar = cookielib.CookieJar()
        jar.set_cookie(_make_jar_cookie())
        request = Request('http://example.com/action', dynamic=True,
                          wait=0, cookiejar=jar,
                          browser_actions=[_failing_browser_action])

        before = _get_cookies_from_cookiejar(request.cookiejar)
        self.assertRaises(BrowserActionError, dh.fetch, request)
        after = _get_cookies_from_cookiejar(request.cookiejar)

        # arbitrary exceptions propagate unchanged for RetryMiddleware,
        # while the semaphore is still returned exactly once
        self.assertSemaphoreAvailable(self.driver_sem)
        self.assertDriverCookiesCleared(driver)
        # the request's cookiejar keeps only its original cookie
        self.assertEqual(before, after)
        self.assertEqual(len(after), 1)
        # cookie harvesting never happened, so nothing half-baked is returned
        self.assertNotIn(('get_cookies',), driver.calls)

    def test_static_request_still_works_after_failed_dynamic_request(self):
        driver = FakeDriver(
            get_error=SeleniumTimeoutException('page load timed out'))
        dh = DownloadHandler(self.spider, driver, self.driver_sem)

        # the dynamic request fails and must not hold the semaphore afterwards
        self.assertRaises(
            TimeoutException,
            dh.fetch,
            Request('http://example.com/slow', dynamic=True, wait=0))
        self.assertSemaphoreAvailable(self.driver_sem)
        self.assertDriverCookiesCleared(driver)

        # the following static request does not touch the driver and must
        # complete normally
        original_requests = downloader_pkg.requests
        downloader_pkg.requests = _FakeRequests
        try:
            response = dh.fetch(Request('http://example.com/static'))
        finally:
            downloader_pkg.requests = original_requests

        self.assertIsInstance(response, Response)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, 'static body')
        self.assertSemaphoreAvailable(self.driver_sem)

    def test_semaphore_survives_repeated_failures(self):
        # BoundedSemaphore raises ValueError on over-release; a leaked or
        # doubly-released permit would make repeated failures explode or hang.
        driver = FakeDriver(
            get_error=SeleniumTimeoutException('page load timed out'))
        dh = DownloadHandler(self.spider, driver, self.driver_sem)
        for _ in range(3):
            self.assertRaises(
                TimeoutException,
                dh.fetch,
                Request('http://example.com/slow', dynamic=True, wait=0))
            self.assertSemaphoreAvailable(self.driver_sem)

    def test_cookie_cleanup_failure_does_not_mask_original_error(self):
        class ExplodingDriver(FakeDriver):
            def delete_all_cookies(self):
                self.calls.append(('delete_all_cookies',))
                raise RuntimeError('cookie cleanup failed')

        driver = ExplodingDriver(
            get_error=SeleniumTimeoutException('page load timed out'))
        dh = DownloadHandler(self.spider, driver, self.driver_sem)
        # original TimeoutException must still win, semaphore still returned
        self.assertRaises(TimeoutException, dh.fetch,
                          Request('http://example.com/slow', dynamic=True))
        self.assertSemaphoreAvailable(self.driver_sem)


if __name__ == "__main__":
    unittest.main()
