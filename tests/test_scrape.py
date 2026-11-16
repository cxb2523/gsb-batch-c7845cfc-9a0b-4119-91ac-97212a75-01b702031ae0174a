"""Tests for scraperwiki.scrape using a local HTTP server (no external network)."""

import inspect
import socket
import threading
import time
import unittest
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import scraperwiki

BODY = b"hello from the local test server\n"


class _Handler(BaseHTTPRequestHandler):
    server_version = "ScrapeTestServer/1.0"

    def _record(self, body=b""):
        self.server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": body,
            }
        )

    def do_GET(self):
        self._record()
        self._route()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self._record(body)
        self._route()

    def _route(self):
        if self.path.startswith("/slow"):
            time.sleep(3)
            self._send(200, b"slow response")
        elif self.path.startswith("/missing"):
            self._send(404, b"not found")
        elif self.path.startswith("/error"):
            self._send(500, b"server error")
        else:
            self._send(200, BODY)

    def _send(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class ScrapeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.server.daemon_threads = True
        cls.server.requests = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.server.requests.clear()


class TestScrapeSuccess(ScrapeTestCase):
    def test_returns_bytes(self):
        result = scraperwiki.scrape(self.base_url + "/ok")
        self.assertIsInstance(result, bytes)
        self.assertEqual(result, BODY)

    def test_returns_bytes_with_params(self):
        result = scraperwiki.scrape(self.base_url + "/ok", params={"a": "1"})
        self.assertIsInstance(result, bytes)
        self.assertEqual(result, BODY)

    def test_params_sent_as_post(self):
        scraperwiki.scrape(
            self.base_url + "/ok", params={"a": "1", "b": "x y", "c": "é"}
        )
        (request,) = self.server.requests
        self.assertEqual(request["method"], "POST")
        self.assertEqual(
            urllib.parse.parse_qs(request["body"].decode("utf-8")),
            {"a": ["1"], "b": ["x y"], "c": ["é"]},
        )

    def test_no_params_is_get(self):
        scraperwiki.scrape(self.base_url + "/ok")
        (request,) = self.server.requests
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["body"], b"")

    def test_user_agent_sent(self):
        scraperwiki.scrape(self.base_url + "/ok", user_agent="my-scraper/1.0")
        (request,) = self.server.requests
        self.assertEqual(request["headers"].get("User-Agent"), "my-scraper/1.0")

    def test_user_agent_and_params_together(self):
        scraperwiki.scrape(
            self.base_url + "/ok", params={"q": "v"}, user_agent="ua-test/2.0"
        )
        (request,) = self.server.requests
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["headers"].get("User-Agent"), "ua-test/2.0")
        self.assertEqual(urllib.parse.parse_qs(request["body"].decode()), {"q": ["v"]})


class TestScrapeErrors(ScrapeTestCase):
    def test_404_raises_scraperwiki_error(self):
        url = self.base_url + "/missing"
        with self.assertRaises(scraperwiki.Error) as caught:
            scraperwiki.scrape(url)

        error = caught.exception
        self.assertIsInstance(error, scraperwiki.ScrapeHTTPError)
        self.assertIsInstance(error, scraperwiki.ScrapeError)
        self.assertEqual(error.status_code, 404)
        self.assertEqual(error.url, url)
        self.assertIn("404", str(error))
        self.assertIn(url, str(error))

    def test_500_raises_scraperwiki_error(self):
        url = self.base_url + "/error"
        with self.assertRaises(scraperwiki.Error) as caught:
            scraperwiki.scrape(url)

        error = caught.exception
        self.assertIsInstance(error, scraperwiki.ScrapeHTTPError)
        self.assertEqual(error.status_code, 500)
        self.assertIn("500", str(error))
        self.assertIn(url, str(error))

    def test_timeout_raises_scraperwiki_error(self):
        url = self.base_url + "/slow"
        started = time.monotonic()
        with self.assertRaises(scraperwiki.Error) as caught:
            scraperwiki.scrape(url, timeout=0.3)
        elapsed = time.monotonic() - started

        error = caught.exception
        self.assertIsInstance(error, scraperwiki.ScrapeTimeoutError)
        self.assertIsInstance(error, scraperwiki.ScrapeError)
        self.assertEqual(error.url, url)
        self.assertIn(url, str(error))
        # The timeout must actually take effect, rather than waiting for the
        # 3 second sleep in the handler.
        self.assertLess(elapsed, 2.5)

    def test_connection_failure_raises_scraperwiki_error(self):
        # Bind a socket to find a port that is (very probably) unused, then
        # close it so that connecting cannot reach a server.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 0))
            host, port = probe.getsockname()[:2]
        finally:
            probe.close()

        url = f"http://{host}:{port}/"
        with self.assertRaises(scraperwiki.Error) as caught:
            scraperwiki.scrape(url, timeout=2)

        # Depending on the platform a refused connection surfaces as either a
        # connection error or a timeout, but both are part of the Error
        # hierarchy and both name the URL.
        error = caught.exception
        self.assertIsInstance(
            error, (scraperwiki.ScrapeNetworkError, scraperwiki.ScrapeTimeoutError)
        )
        self.assertEqual(error.url, url)
        self.assertIn(url, str(error))

    def test_urlerror_maps_to_network_error(self):
        url = "http://example.invalid/"
        failure = urllib.error.URLError(ConnectionRefusedError("refused"))
        with (
            mock.patch.object(
                scraperwiki.utils.urllib.request, "urlopen", side_effect=failure
            ),
            self.assertRaises(scraperwiki.ScrapeNetworkError) as caught,
        ):
            scraperwiki.scrape(url)

        error = caught.exception
        self.assertIsInstance(error, scraperwiki.Error)
        self.assertEqual(error.url, url)
        self.assertIn(url, str(error))
        self.assertIn("refused", str(error))

    def test_urlerror_wrapping_timeout_maps_to_timeout_error(self):
        url = "http://example.invalid/"
        failure = urllib.error.URLError(TimeoutError("timed out"))
        with (
            mock.patch.object(
                scraperwiki.utils.urllib.request, "urlopen", side_effect=failure
            ),
            self.assertRaises(scraperwiki.ScrapeTimeoutError) as caught,
        ):
            scraperwiki.scrape(url, timeout=1.5)

        error = caught.exception
        self.assertIsInstance(error, scraperwiki.Error)
        self.assertEqual(error.url, url)
        self.assertIn(url, str(error))

    def test_timeout_default_is_none(self):
        default = inspect.signature(scraperwiki.scrape).parameters["timeout"].default
        self.assertIsNone(default)

    def test_error_hierarchy(self):
        for cls in (
            scraperwiki.ScrapeError,
            scraperwiki.ScrapeHTTPError,
            scraperwiki.ScrapeNetworkError,
            scraperwiki.ScrapeTimeoutError,
        ):
            self.assertTrue(issubclass(cls, scraperwiki.Error))


if __name__ == "__main__":
    unittest.main()
