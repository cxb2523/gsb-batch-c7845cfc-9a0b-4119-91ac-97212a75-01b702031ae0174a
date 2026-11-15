"""
Local end-to-end verification for the rewritten crawler.

Spin up a tiny static site (with in-site link cycles, off-site links,
a 404 page and fragment-only variants) and check that crawl():

  1. terminates on its own,
  2. fetches every same-host normalized URL exactly once,
  3. never requests an off-site link,
  4. records a readable failure reason for the 404,
  5. keeps concurrent workers' records separated by URL attribution,
  6. returns success / failed / skipped_external counts.

Run from anywhere:

    python -I tests/verify_crawler.py
"""

import os
import sys
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from Web_Crawler.crawler import (  # noqa: E402
    ERROR_LOG_FILE,
    LOG_FILE,
    DUMP_FILE,
    crawl,
    normalize_url,
)

FIXTURE_DIR = REPO_ROOT / "tests" / "fixture_site"
CRAWL_TIMEOUT = 60  # the whole crawl must finish well within this


class RecordingHandler(SimpleHTTPRequestHandler):
    """Static-file handler that also records every path requested."""

    requested_paths = []
    _log_lock = threading.Lock()

    def do_GET(self):
        with self._log_lock:
            self.requested_paths.append(self.path.split("#", 1)[0])
        super().do_GET()

    def log_message(self, *args):
        pass


class CrawlerVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._cwd = os.getcwd()
        # Bind the fixture directory explicitly: the tests chdir into
        # temporary output directories, and the server must keep serving
        # the fixture regardless of the process-wide cwd.
        handler = partial(
            RecordingHandler, directory=str(FIXTURE_DIR)
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.base = "http://127.0.0.1:{}/".format(cls.port)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, name="fixture-http", daemon=True
        )
        cls.thread.start()
        os.chdir(str(FIXTURE_DIR))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        os.chdir(cls._cwd)

    def _run_crawl(self, workdir, *urls):
        """Run crawl() in a fresh output directory with a hard timeout."""
        os.chdir(workdir)
        try:
            RecordingHandler.requested_paths.clear()
            worker_error = []

            def target(box):
                try:
                    box.append(crawl(*urls))
                except Exception as exc:  # pragma: no cover - failure surface
                    box.append(exc)

            box = []
            runner = threading.Thread(target=target, args=(box,), daemon=True)
            runner.start()
            runner.join(timeout=CRAWL_TIMEOUT)
            self.assertFalse(
                runner.is_alive(),
                "crawl() did not terminate within {}s".format(CRAWL_TIMEOUT),
            )
            self.assertEqual(
                len(box), 1, "crawl thread raised: {!r}".format(box[:1])
            )
            self.assertNotIsInstance(box[0], BaseException)
            return box[0]
        finally:
            os.chdir(str(FIXTURE_DIR))

    def _read(self, workdir, name):
        return (Path(workdir) / name).read_text(encoding="utf-8")

    def test_single_start_url_lifecycle(self):
        expected_pages = {
            self.base,
            self.base + "index.html",
            self.base + "page1.html",
            self.base + "page2.html",
        }
        expected_external = {
            "https://external.example.com/path",
            "http://external.example.com/path2",
            "https://external.example.com/other",
        }

        with TemporaryDirectory() as workdir:
            results = self._run_crawl(workdir, self.base)

        # --- observable result: one CrawlResult per start URL ---------
        self.assertEqual(set(results), {self.base})
        self.assertEqual(len(results.results), 1)
        result = results[self.base]
        self.assertEqual(result.success, 4)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.skipped_external, 3)
        # aggregate counters on the summary
        self.assertEqual(
            (results.success, results.failed, results.skipped_external),
            (4, 1, 3),
        )

        self.assertEqual(set(result.successful_urls), expected_pages)
        self.assertEqual(len(result.failed_urls), 1)
        failed_url, reason = result.failed_urls[0]
        self.assertEqual(failed_url, self.base + "missing.html")
        self.assertTrue(
            reason.startswith("HTTP 404"),
            "expected readable HTTP 404 reason, got: {!r}".format(reason),
        )
        self.assertEqual(set(result.external_urls), expected_external)

        # --- the local server never saw an off-site or mailto request -
        host_paths = set(RecordingHandler.requested_paths)
        self.assertNotIn("https://external.example.com/path", host_paths)
        for marker in ("external", "mailto"):
            self.assertFalse(
                any(marker in path for path in host_paths),
                "off-site request reached the server: {!r}".format(host_paths),
            )

        # --- every same-host page fetched exactly once ----------------
        fetch_counts = {
            path: RecordingHandler.requested_paths.count(path)
            for path in host_paths
        }
        self.assertEqual(
            sorted(host_paths),
            ["/", "/index.html", "/missing.html", "/page1.html",
             "/page2.html"],
        )
        for path in ("/", "/index.html", "/page1.html", "/page2.html"):
            self.assertEqual(
                fetch_counts[path], 1,
                "{} fetched {} times".format(path, fetch_counts[path]),
            )
        self.assertEqual(fetch_counts["/missing.html"], 1)

        # --- log.txt: one line per successful URL, no duplicates ------
        with TemporaryDirectory() as workdir:
            self._run_crawl(workdir, self.base)
            log_lines = [
                line for line in self._read(workdir, LOG_FILE).splitlines()
                if line.strip()
            ]
            self.assertEqual(sorted(log_lines), sorted(expected_pages))
            self.assertEqual(len(log_lines), len(set(log_lines)))

            # --- error_log.txt: URL plus readable reason --------------
            error_lines = [
                line for line in self._read(
                    workdir, ERROR_LOG_FILE
                ).splitlines()
                if line.strip()
            ]
            self.assertEqual(len(error_lines), 1)
            self.assertIn("missing.html", error_lines[0])
            self.assertIn("HTTP 404", error_lines[0])

            # --- dump.txt: page bodies were appended ------------------
            dump = self._read(workdir, DUMP_FILE)
            for marker in (
                "INDEX_BODY_MARKER",
                "PAGE1_BODY_MARKER",
                "PAGE2_BODY_MARKER",
            ):
                self.assertIn(marker, dump)
            for page in expected_pages:
                self.assertIn(page, dump)

    def test_concurrent_start_urls_share_state_safely(self):
        page1 = self.base + "page1.html"
        page2 = self.base + "page2.html"

        with TemporaryDirectory() as workdir:
            results = self._run_crawl(workdir, page1, page2)

        # Per-worker counts depend on thread scheduling, so assert the
        # deterministic aggregate outcome instead: both tasks report, the
        # pages are each fetched once and only one 404 is recorded.
        self.assertEqual(set(results), {page1, page2})
        self.assertEqual(len(results.results), 2)
        all_success = (
            results[page1].successful_urls
            + results[page2].successful_urls
        )
        self.assertEqual(len(all_success), len(set(all_success)))
        all_pages = {
            self.base,
            self.base + "index.html",
            self.base + "page1.html",
            self.base + "page2.html",
        }
        self.assertTrue(set(all_success).issubset(all_pages))
        # Both starting pages were crawled, plus whatever links were
        # discovered before the shared dedup set was populated.
        self.assertIn(page1, all_success)
        self.assertIn(page2, all_success)
        self.assertEqual(results[page1].failed + results[page2].failed, 1)
        all_external = set(
            results[page1].external_urls + results[page2].external_urls
        )
        self.assertEqual(
            all_external,
            {
                "https://external.example.com/path",
                "http://external.example.com/path2",
                "https://external.example.com/other",
            },
        )

        # One shared dedup set => the fixture server saw each page once.
        self.assertTrue(
            set(RecordingHandler.requested_paths).issubset(
                {"/", "/index.html", "/missing.html",
                 "/page1.html", "/page2.html"}
            )
        )
        for path in set(RecordingHandler.requested_paths):
            self.assertEqual(
                RecordingHandler.requested_paths.count(path), 1,
                "{} fetched more than once".format(path),
            )

        # Records stay attributable: every log line belongs to one task.
        with TemporaryDirectory() as workdir:
            self._run_crawl(workdir, page1, page2)
            log_lines = [
                line for line in self._read(workdir, LOG_FILE).splitlines()
                if line.strip()
            ]
            self.assertEqual(len(log_lines), len(set(log_lines)))
            for line in log_lines:
                self.assertTrue(
                    line.startswith(self.base),
                    "record from an unexpected task: {!r}".format(line),
                )
            error_lines = [
                line for line in self._read(
                    workdir, ERROR_LOG_FILE
                ).splitlines()
                if line.strip()
            ]
            self.assertTrue(all("HTTP 404" in line for line in error_lines))

    def test_normalization_rules(self):
        self.assertEqual(
            normalize_url("http://Example.com:80/a/b#frag"),
            "http://example.com/a/b",
        )
        self.assertEqual(
            normalize_url("https://Example.com:443"),
            "https://example.com/",
        )
        self.assertEqual(
            normalize_url("https://example.com/page#x"),
            "https://example.com/page",
        )
        # Non-http(s) schemes cannot be crawled.
        self.assertEqual(normalize_url("mailto:test@example.com"), "")
        self.assertEqual(normalize_url("javascript:alert(1)"), "")
        self.assertEqual(normalize_url(""), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
