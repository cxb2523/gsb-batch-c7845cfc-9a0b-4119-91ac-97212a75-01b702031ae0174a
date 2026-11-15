#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    ______________________________________________________________________
   |                         |                  |                         |
   |                   +-----^--TorSpider-v0.8--^-----+                   |
   |                   |  Crawling the Invisible Web  |                   |
   |                   +------------------------------+                   |
   |                                                                      |
   | TorSpider employs an army of spiders to explore Tor hidden services, |
   | seeking to uncover and catalogue the deepest reaches of the darknet. |
    ––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––
    # +----------------------------------------+ #
    # |       Beware, ye who enter here:       | #
    # |     The Invisible Web is rife with     | #
    # | wondrous and terrible things. It is no | #
    # |  place for the squeamish or the faint  | #
    # |    of heart. Here there be dragons!    | #
    # +----------------------------------------+ #

"""

import os
import sys
import time
import json
import configparser
import urllib.parse
from libs.functions import *
from libs.logging import logger
from urllib.parse import urlsplit, urlunsplit
from multiprocessing import cpu_count, Process
from libs.parsers import get_forms, get_links, get_title
from libs.classes import SpiderURL

try:
    # 'names' is only used to give the worker processes friendly names. Keep
    # the import optional so the crawling logic can be imported and tested
    # without the dependency installed.
    import names
except ImportError:  # pragma: no cover - depends on the local environment
    names = None

'''---[ GLOBAL VARIABLES ]---'''

# The current release version.
version = '0.8'

# The configuration is normally loaded in __main__ below. These helpers let the
# rest of the module (and tests) use the values without depending on import
# order.
def _config_value(name, default=''):
    return globals().get(name, default)


def is_valid_onion_url(url):
    # Return True only for a url that points directly at a .onion host.
    # Non-onion redirect targets must never be fetched.
    if not url:
        return False
    try:
        parts = urlsplit(url)
    except Exception:
        return False
    host = (parts.hostname or '').lower()
    if not host.endswith('.onion'):
        return False
    if '.onion.' in host:
        # Onion redirector domains such as x.onion.to are not valid targets.
        return False
    if parts.scheme and parts.scheme not in ('http', 'https'):
        return False
    # Reject a bare '.onion' with no service label.
    return bool(host[:-len('.onion')])


'''---[ CLASS DEFINITIONS ]---'''


class Spider:
    def __init__(self, session=None, poster=None, node_name_override=None):
        self.api_url = _config_value('api_url')
        self.node_name = (node_name_override
                          if node_name_override is not None
                          else _config_value('node_name', None))
        self.headers = self.__gen_api_header()
        self.session = session if session is not None else get_tor_session()
        # The callable that ships a scan_result to the backend. Injecting one
        # makes the crawl loop testable without a live API; when it is None the
        # normal API call is used.
        self._poster = poster

    @staticmethod
    def __gen_api_header():
        # Create a header for the API connection.
        myhead = dict()
        myhead['Content-Type'] = 'application/json'
        myhead['Authorization'] = 'Token {}'.format(
            _config_value('api_key'))
        myhead['Authorization-Node'] = _config_value('api_node')
        return myhead

    def __get_query(self, endpoint, query):
        # Request data from the backend API.
        logger.log("Running GET Query on endpoint: {}".format(endpoint),
                   'debug')
        # Send the request for information from the API.
        r = requests.get(
            self.api_url + endpoint + '?q=' + urllib.parse.quote_plus(
                json.dumps(query)),
            headers=self.headers,
            verify=_config_value('ssl_verify', True))
        if r.status_code == 200:
            # If successful then it returns the object data.
            logger.log('GET Query successful for endpoint: {}'.format(endpoint),
                       'debug')
            return json.loads(r.text).get('objects')
        elif r.status_code == 401:
            # Unauthorized.
            logger.log('Receive 401 Unauthorized', 'error')
            return {}
        else:
            # Some other failure.
            return {}

    def __post_parse(self, data):
        # Enqueue the data to be parsed on the backend
        logger.log('Pushing to parse queue.', 'debug')
        # Send the data to the backend API.
        r = requests.post(
            self.api_url + 'parse',
            headers=self.headers,
            data=data,
            verify=_config_value('ssl_verify', True))
        if r.status_code == 201:
            # If created then it returns the object data.
            logger.log('Added successfully', 'debug')
            return json.loads(r.text)
        elif r.status_code == 401:
            # Unauthorized.
            logger.log('Receive 401 Unauthorized', 'error')
            return {}
        else:
            # Some other failure.
            return {}

    # The following lists define possible response codes that a server might
    # send in reply to our request for their url.
    #
    # Redirect codes: These status codes redirect to other pages. The target
    # is kept on the scan_result, and followed when it is a valid .onion url.
    REDIRECT_CODES = [301, 302, 303, 307, 308]

    # Fault codes: These status codes imply that there was something wrong
    # with the page being requested, such as being non-existent.
    FAULT_CODES = [400, 401, 403, 404, 405, 406, 410, 413, 414, 444, 451,
                   495, 496, 500, 501, 502, 505, 508, 511]

    # No-Fault codes: These imply that something temporarily went wrong, but
    # it's possible that it might work in the future.
    NO_FAULT_CODES = [408, 421, 423, 429, 503, 504]

    # Good codes: These are the codes we want to see when accessing a web
    # service.
    GOOD_CODES = [200, 201]

    # The maximum number of redirects followed while fetching a page. This
    # also bounds a redirect chain discovered in the body request.
    MAX_FETCH_REDIRECTS = 5

    def crawl(self):
        logger.log("Ready to explore!", 'info')
        while True:
            # To stop the script, simply create an empty file called 'sleep'
            # in the directory where TorSpider.py resides.
            if os.path.exists('sleep'):
                # If the 'sleep' file is detected, TorSpider knows that it
                # is time to sleep.
                break

            # Ask the API for a url to scan.
            try:
                next_url_info = self.__get_query(
                    'next', {"node_name": self.node_name})
            except Exception as e:
                # A failed query must not kill the worker. Wait and retry.
                logger.log('Could not query the API for a url: {}'.format(e),
                           'error')
                time.sleep(30)
                continue
            if (not next_url_info
                    or 'hash' not in next_url_info.keys()
                    or 'url' not in next_url_info.keys()):
                # There are currently no urls to scan.
                logger.log('We found no urls to check, sleeping for 30 seconds.', 'debug')
                # Wait thirty seconds before trying again.
                time.sleep(30)
                continue

            # We successfully retrieved a url from the API.
            url = next_url_info['url']
            last_hash = next_url_info['hash']
            if not last_hash:
                last_hash = ''
            logger.log('Found next url: {}'.format(url), 'debug')

            # Scan the url. This call always reports exactly one scan_result,
            # no matter which stage the scan stops at.
            self.scan_and_report(url, last_hash)

        # If we reach this point, the main loop is finished and the spiders are
        # going to sleep.
        logger.log("Going to sleep!", 'info')

    def scan_and_report(self, url, last_hash=''):
        """Scan a single url and report the result exactly once.

        Every code path in a scan - success, redirect, bad content type,
        parse failure, network error - ends here with one call to the poster.
        The populated SpiderURL is returned so callers (and tests) can inspect
        it.
        """
        scan_result = SpiderURL()
        scan_result.url = url
        scan_result.last_node = self.node_name

        try:
            self.scan_url(scan_result, url, last_hash)
        except Exception as e:
            # scan_url is expected to handle request errors itself. This is a
            # final safety net so an unexpected failure still produces a
            # reportable result instead of escaping the crawl loop.
            logger.log('Unhandled exception while scanning {}: {}'.format(
                url, e), 'error')
            if not scan_result.fault:
                scan_result.fault = 'unexpected error'
            scan_result.fault_reason = 'unexpected exception ({}: {})'.format(
                e.__class__.__name__, str(e).strip() or 'no detail')
            scan_result.scan_stage = 'error'

        try:
            if self._poster is not None:
                self._poster(scan_result.to_json())
            else:
                self.__post_parse(scan_result.to_json())
        except Exception as e:
            # A reporting failure must not kill the worker; log it and move
            # on to the next url.
            logger.log('Could not report scan result for {}: {}'.format(
                url, e), 'error')

        return scan_result

    def scan_url(self, scan_result, url, last_hash=''):
        """Populate scan_result for a single url.

        Expected request failures are recorded on the result instead of being
        raised, so the caller always has something to report.
        """
        try:
            self._scan_url(scan_result, url, last_hash)
        except requests.exceptions.InvalidURL:
            # The url provided was invalid.
            logger.log("Invalid url: {}".format(url), 'error')
            scan_result.fault = 'invalid url'
            scan_result.fault_reason = 'the url could not be parsed'
            scan_result.scan_stage = 'request'
        except requests.exceptions.InvalidSchema:
            # We got an invalid schema. Add the url with both http and https
            # schemas to the database to see if those work.
            scan_result.fault = 'invalid schema'
            scan_result.fault_reason = 'the url has an unsupported scheme'
            scan_result.scan_stage = 'request'
            try:
                (s, n, p, q, f) = urlsplit(url)
                for scheme in ['http', 'https']:
                    new_url = urlunsplit((scheme, n, p, q, f))
                    if '.onion' in new_url and '.onion.' not in new_url:
                        # Ignore any non-onion domain.
                        scan_result.new_urls.append(new_url)
            except Exception as e:
                logger.log('Could not rebuild schemas for {}: {}'.format(
                    url, e), 'error')
        except requests.exceptions.SSLError as e:
            # There was a problem with the site's SSL certificate.
            logger.log("SSL Error at {}: {}".format(url, e), 'error')
            scan_result.fault = 'Bad SSL'
            scan_result.fault_reason = 'SSL error: {}'.format(
                str(e).strip() or 'unknown')
            scan_result.scan_stage = 'request'
        except requests.exceptions.ConnectionError as e:
            # We had trouble connecting to the url. The result is still
            # reported; the backend sees online=False and the reason below.
            logger.log("Connection error to url: {} ({})".format(url, e),
                       'debug')
            scan_result.fault_reason = 'connection error: {}'.format(
                str(e).strip() or 'unknown')
            scan_result.scan_stage = 'request'
        except requests.exceptions.Timeout:
            # It took too long to load this page.
            logger.log('Request timed out: {}'.format(url), 'debug')
            scan_result.fault_reason = 'request timed out'
            scan_result.scan_stage = 'request'
        except requests.exceptions.TooManyRedirects:
            # Redirected too many times. Let's not keep trying.
            scan_result.fault = 'redirect'
            scan_result.fault_reason = 'too many redirects'
            scan_result.scan_stage = 'redirect'
        except requests.exceptions.ChunkedEncodingError as e:
            # Server gave bad chunk. Previously this path silently skipped
            # the url; it now reports a readable fault.
            logger.log('Chunked encoding error at {}: {}'.format(url, e),
                       'error')
            scan_result.fault = 'chunked encoding error'
            scan_result.fault_reason = 'bad chunk in response: {}'.format(
                str(e).strip() or 'unknown')
            scan_result.scan_stage = 'request'
        except MemoryError:
            # Whatever it is, it's way too big.
            logger.log('Ran out of memory: {}'.format(url), 'error')
            scan_result.fault = 'memory error'
            scan_result.fault_reason = 'the response was too large to process'
            scan_result.scan_stage = 'parse'
        except NotImplementedError as e:
            logger.log("I don't know what this means: {} - {}".format(e, url),
                       'error')
            scan_result.fault = 'unsupported response'
            scan_result.fault_reason = 'response could not be handled: {}'.format(
                str(e).strip() or 'unknown')
            scan_result.scan_stage = 'parse'
        except Exception as e:
            # Never let an unexpected failure escape without a report.
            logger.log('Unknown exception: {} ({})'.format(e, url), 'error')
            scan_result.fault = 'unexpected error'
            scan_result.fault_reason = 'unexpected exception ({}: {})'.format(
                e.__class__.__name__, str(e).strip() or 'no detail')
            scan_result.scan_stage = 'error'

    def _scan_url(self, scan_result, url, last_hash=''):
        # Attempt to retrieve the page's headers.
        logger.log('Getting head of url: {}'.format(url), 'debug')
        scan_result.scan_stage = 'request'
        head = self.session.head(url, timeout=30, allow_redirects=False)
        scan_result.status_code = getattr(head, 'status_code', None)
        scan_result.scan_stage = 'head'

        fetch_url = url
        followed_redirect = False

        # Analyze the status code sent by the server.
        if head.status_code in self.REDIRECT_CODES:
            # The url results in a redirection.
            logger.log('Found a redirection url: {} code: {}'.format(
                url, head.status_code), 'debug')
            scan_result.redirect_status = head.status_code
            target = self._get_redirect_target(head, url)
            # Store information about where this url redirects, even when we
            # cannot follow it.
            scan_result.redirect = target

            if target is None:
                # The server did not provide a usable redirect url. Keep the
                # original 'fault' value (the status code) so old backend
                # logic sees the same thing, and record the readable reason in
                # the additive fields.
                scan_result.fault = str(head.status_code)
                scan_result.redirect_fault = (
                    'redirect response (code {}) did not include a usable '
                    'Location header'.format(head.status_code))
                scan_result.fault_reason = scan_result.redirect_fault
                scan_result.scan_stage = 'redirect'
                logger.log('{}: {}'.format(url, scan_result.fault_reason),
                           'error')
                return

            if not is_valid_onion_url(target):
                # Never fetch a redirect target that is not a .onion
                # address; only record why we stopped. 'fault' keeps its
                # original status-code value for backward compatibility.
                scan_result.fault = str(head.status_code)
                scan_result.redirect_fault = (
                    'refusing to follow redirect to non-onion target: '
                    '{}'.format(target))
                scan_result.fault_reason = scan_result.redirect_fault
                scan_result.scan_stage = 'redirect'
                logger.log('{}: {}'.format(url, scan_result.fault_reason),
                           'error')
                return

            # The redirect target is a valid .onion address. Keep it and
            # continue scanning the page it points at.
            if target not in scan_result.new_urls:
                scan_result.new_urls.append(target)
            scan_result.online = True
            fetch_url = target
            followed_redirect = True
            logger.log('Following redirect target: {}'.format(target), 'debug')

        elif head.status_code in self.FAULT_CODES:
            # We received a fault code from the server.
            logger.log('Found a fault in url: {} code: {}'.format(
                url, head.status_code), 'debug')
            scan_result.fault = str(head.status_code)
            scan_result.fault_reason = 'server returned fault status {}'.format(
                head.status_code)
            scan_result.scan_stage = 'head'
            return

        elif head.status_code in self.NO_FAULT_CODES:
            # The url results in a problem, but not a fault.
            logger.log('Found a problem url: {} code: {}'.format(
                url, head.status_code), 'debug')
            scan_result.fault_reason = 'server returned temporary status {}'.format(
                head.status_code)
            scan_result.scan_stage = 'head'
            return

        elif head.status_code not in self.GOOD_CODES:
            # Unknown status. More status codes will be added as they are
            # discovered in the wild.
            logger.log('Found a unknown status url: {} code: {}'.format(
                url, head.status_code), 'debug')
            logger.log("Unknown status code {}: {}".format(
                head.status_code, url), 'error')
            scan_result.fault = str(head.status_code)
            scan_result.fault_reason = 'server returned unknown status {}'.format(
                head.status_code)
            scan_result.scan_stage = 'head'
            return

        # If we reach this point, we know the domain is online.
        scan_result.online = True
        scan_result.scan_stage = 'content_type'

        # We only want to scan plaintext files, not binary data or images.
        # Check the content type before processing the page. A redirect's
        # head response describes the redirect itself, so rely on the follow-up
        # request headers in that case.
        if followed_redirect:
            head_type = None
        else:
            head_type = get_type(head.headers)
            scan_result.content_type = head_type
            logger.log("Found content type of url: {} as: {}".format(
                url, head_type), 'debug')
            if head_type is not None and head_type != 'text':
                # This content is not text-based, so don't scan it.
                scan_result.fault = 'type: {0}'.format(head_type)
                scan_result.fault_reason = (
                    'server reported non-text content type: {}'.format(
                        head_type))
                scan_result.scan_stage = 'content_type'
                return

        request, followed_during_fetch = self._fetch_page(
            scan_result, fetch_url)
        if request is None:
            # The page could not be fetched without violating the redirect
            # policy. The reason has already been recorded on the result.
            return
        scan_result.status_code = getattr(
            request, 'status_code', scan_result.status_code)

        # The final response after any redirects may itself be an error page.
        # Record that instead of parsing an error body as if it were content.
        fetched_redirect_target = followed_redirect or followed_during_fetch
        if (fetched_redirect_target
                and scan_result.status_code in self.FAULT_CODES):
            scan_result.online = False
            scan_result.fault = str(scan_result.status_code)
            scan_result.fault_reason = (
                'redirect target returned fault status {}'.format(
                    scan_result.status_code))
            scan_result.scan_stage = 'head'
            return

        if head_type is None or followed_during_fetch:
            # The content type in the head response is unreliable here: either
            # the headers did not report one, or the page we actually fetched
            # is a different url (a redirect target). Try the full response
            # headers before giving up.
            request_type = get_type(request.headers)
            logger.log("Found content type of url: {} as: {}".format(
                fetch_url, request_type), 'debug')
            if request_type is not None and request_type != 'text':
                # We got a non-text content type, such as a binary or an
                # image file.
                scan_result.content_type = request_type
                scan_result.fault = 'type: {}'.format(request_type)
                scan_result.fault_reason = (
                    'response reported non-text content type: {}'.format(
                        request_type))
                scan_result.scan_stage = 'content_type'
                return
            if request_type is None:
                # We still cannot confirm the type. Parse it as text on a
                # best-effort basis and record that the type is unknown,
                # rather than dropping the page.
                scan_result.content_type = 'unknown'
                scan_result.type_unknown = True
                logger.log('Content type unknown for url: {}; attempting to '
                           'parse as text.'.format(fetch_url), 'warning')
            else:
                scan_result.content_type = 'text'

        # Parse the page. Any failure here is recorded as a readable fault
        # instead of escaping as an unhandled exception.
        scan_result.scan_stage = 'parse'
        try:
            page_text = request.text
        except Exception as e:
            self._record_parse_failure(scan_result, e, fetch_url)
            return

        # Get the title of the page. A title parse failure is not fatal; the
        # rest of the page can still be processed.
        page_title = 'Unknown'
        try:
            page_title = get_title(page_text)
        except Exception as e:
            logger.log('Could not parse title for url: {} ({})'.format(
                fetch_url, e), 'debug')

        # Set the title of the url before checking the hash, just in case the
        # title wasn't set during the last scan.
        scan_result.title = page_title
        logger.log('Page title for url: {} is: {}'.format(
            fetch_url, page_title), 'debug')

        try:
            # Let's see if the page has changed.
            page_hash = get_hash(request.content)
            logger.log('Page hash of url: {} is: {}'.format(
                fetch_url, page_hash), 'debug')
            logger.log('Last page hash of url: {} is: {}'.format(
                url, last_hash), 'debug')

            # If the hash hasn't changed, don't process the page.
            if last_hash == page_hash:
                logger.log('The hashes matched, nothing has changed.', 'debug')
                scan_result.scan_stage = 'unchanged'
                return

            scan_result.hash = page_hash

            # The page's HTML changed since our last scan; let's process it.
            # Links are resolved against the page actually fetched, which is
            # the redirect target when a redirect was followed.
            page_links = get_links(page_text, fetch_url)

            # Add the links to the database.
            for link_url in page_links:
                if '.onion' in link_url and '.onion.' not in link_url:
                    # Ignore any non-onion domain.
                    scan_result.new_urls.append(link_url)

            # Parse any forms on the page.
            logger.log('Parsing forms on url: {}'.format(fetch_url), 'debug')
            page_forms = get_forms(page_text)

            # Add the forms to the database.
            for form in page_forms:
                # Process the form's information.
                # TODO: Let the backend parse the form dict.
                scan_result.form_dicts.append(dict(form))

            # Parsing is complete for this page!
            scan_result.scan_stage = 'complete'
        except Exception as e:
            self._record_parse_failure(scan_result, e, fetch_url)

    def _fetch_page(self, scan_result, url):
        """Fetch a page, following redirects only to valid .onion targets.

        Returns ``(response, followed)`` where ``followed`` says whether at
        least one redirect hop was taken. ``response`` is None when the
        redirect policy forbids continuing; in that case the reason is
        recorded on ``scan_result``. Redirects are followed manually
        (``allow_redirects`` is off) so that a non-onion hop is never fetched
        implicitly.
        """
        current = url
        followed = False
        for _ in range(self.MAX_FETCH_REDIRECTS + 1):
            request = self.session.get(current, timeout=30,
                                       allow_redirects=False)
            status = getattr(request, 'status_code', None)
            if status not in self.REDIRECT_CODES:
                return request, followed

            scan_result.redirect_status = status
            target = self._get_redirect_target(request, current)
            if scan_result.redirect is None:
                scan_result.redirect = target
            if target is None:
                # Keep 'fault' as the status code, matching the original
                # redirect behaviour, and record the readable reason in the
                # additive fields.
                scan_result.fault = str(status)
                scan_result.redirect_fault = (
                    'redirect response (code {}) did not include a usable '
                    'Location header'.format(status))
                scan_result.fault_reason = scan_result.redirect_fault
                scan_result.scan_stage = 'redirect'
                logger.log('{}: {}'.format(current, scan_result.fault_reason),
                           'error')
                return (None, followed)
            if not is_valid_onion_url(target):
                scan_result.fault = str(status)
                scan_result.redirect_fault = (
                    'refusing to follow redirect to non-onion target: '
                    '{}'.format(target))
                scan_result.fault_reason = scan_result.redirect_fault
                scan_result.scan_stage = 'redirect'
                logger.log('{}: {}'.format(current, scan_result.fault_reason),
                           'error')
                return (None, followed)
            if target not in scan_result.new_urls:
                scan_result.new_urls.append(target)
            current = target
            followed = True

        # Too many hops. Report it as a redirect fault rather than looping.
        scan_result.fault = 'redirect'
        scan_result.fault_reason = (
            'too many redirects (more than {})'.format(
                self.MAX_FETCH_REDIRECTS))
        scan_result.scan_stage = 'redirect'
        logger.log('{}: {}'.format(url, scan_result.fault_reason), 'error')
        return (None, followed)

    @staticmethod
    def _get_redirect_target(head, url):
        # Return the absolute redirect target, or None when the server did not
        # provide a usable Location header.
        try:
            location = head.headers['location']
        except Exception:
            try:
                location = head.headers['Location']
            except Exception:
                location = None
        if not location:
            return None
        try:
            # Combine the provided url with the url we scanned in order to
            # fill in any blanks in the redirect.
            return merge_urls(location, url)
        except Exception as e:
            logger.log('{}: could not process redirect target {} ({})'.format(
                url, location, e), 'error')
            # Fall back to the raw target; validation will decide whether it
            # may be followed.
            return location

    @staticmethod
    def _record_parse_failure(scan_result, error, url):
        # Turn a parsing failure into a readable fault on the result. The
        # scan_result is still reported, so the backend can see where the
        # scan stopped.
        detail = str(error).strip() or error.__class__.__name__
        reason = 'could not parse page as text ({}: {})'.format(
            error.__class__.__name__, detail)
        logger.log('Parse failure for url: {} ({})'.format(url, reason),
                   'error')
        if not scan_result.fault:
            scan_result.fault = 'parse error'
        scan_result.fault_reason = reason
        scan_result.scan_stage = 'parse'


'''---[ SCRIPT ]---'''

if __name__ == '__main__':
    if not os.path.exists('spider.cfg'):
        # If we don't yet have a configuration file, make one and tell the
        # user to set it up before continuing.
        default_config = configparser.RawConfigParser()
        default_config.optionxform = lambda option: option
        default_config['TorSpider'] = {
            'LogToConsole': 'True',
        }
        default_config['API'] = {
            'API_URL': 'https://api.torspider.pro/api/',
            'API_KEY': 'Configure_api_key',
            'API_NODE': 'Configure_api_node',
            'VERIFY_SSL': True
        }
        default_config['LOGGING'] = {
            'loglevel': 'INFO'
        }
        with open('spider.cfg', 'w') as config_file:
            default_config.write(config_file)
        print('Default configuration stored in spider.cfg.')
        print('Please edit spider.cfg before running TorSpider again.')
        sys.exit(0)

    # Load the configuration file.
    try:
        config = configparser.ConfigParser()
        config.read('spider.cfg')
        node_name = os.environ.get('API_NODE', None)
        if not node_name:
            node_name = config['API'].get('API_NODE')
        api_url = os.environ.get('API_URL', None)
        if not api_url:
            api_url = config['API'].get('API_URL')
        api_key = os.environ.get('API_KEY', None)
        if not api_key:
            api_key = config['API'].get('API_KEY')
        api_node = os.environ.get('API_NODE', None)
        if not api_node:
            api_node = config['API'].get('API_NODE')
        if api_key == 'Configure_api_key' or api_node == 'Configure_api_node':
            print('You have not configured your API Key and Node.  Please update your spider.cfg file.')
            sys.exit(0)
        ssl_verify = os.environ.get('VERIFY_SSL', None)
        if not ssl_verify:
            ssl_verify = config['API'].getboolean('VERIFY_SSL')
        if not ssl_verify:
            # if we disable ssl verification, we'll also disable warning messages.
            from requests.packages.urllib3.exceptions import InsecureRequestWarning

            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    except Exception as e:
        print('Could not parse spider.cfg. Please verify its syntax.')
        sys.exit(0)
    logger.log('-' * 40, 'info')
    logger.log('TorSpider v{} Initializing...'.format(version), 'info')

    # Create a Tor session and check if it's working.
    logger.log("Establishing Tor connection...", 'info')
    session = get_tor_session()
    while True:
        try:
            logger.log("Verifying Tor connection...", 'info')
            local_ip = get_my_ip(None)
            if not local_ip:
                logger.log("Cannot determine local IP address.", 'error')
            tor_ip = get_my_ip(session)
            if not tor_ip:
                logger.log("Cannot determine tor IP address.", 'error')
            if local_ip == tor_ip:
                logger.log("Tor connection failed: IPs match.", 'error')
            else:
                logger.log("Tor connection established.", 'info')
                break
        except Exception as e:
            logger.log("Tor connection failed: {}".format(e), 'error')
            time.sleep(5)

    # Awaken the spiders!
    Spiders = []
    Spider_Procs = []

    logger.log('Waking the Spiders...', 'info')
    my_names = []

    # We'll start two processes for every processor.
    count = (cpu_count() * 2)
    for x in range(count):
        spider = Spider()
        spider_proc = Process(target=spider.crawl)
        if names is not None:
            spider_proc.name = names.get_first_name()
            while spider_proc.name in my_names:
                spider_proc.name = names.get_first_name()
        else:
            spider_proc.name = 'Spider-{}'.format(x)
        my_names.append(spider_proc.name)
        Spider_Procs.append(spider_proc)
        Spiders.append(spider)
        spider_proc.start()
        # We make them sleep a second so they don't all go skittering after
        # the same url at the same time.
        time.sleep(1)

    for spider_proc in Spider_Procs:
        spider_proc.join()

    try:
        os.unlink('sleep')
    except Exception as e:
        pass
    logger.log('The Spiders have gone to sleep. ZzZzz...', 'info')
    logger.log('-' * 40, 'info')
