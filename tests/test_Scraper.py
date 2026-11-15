#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import requests

from zvg_portal import scraper as scraper_module
from zvg_portal.model import DetailFetchFailure, Land, ObjektEntry, RawAnhang, RawEntry, RawList
from zvg_portal.scraper import ZvgPortal


class FakeResponse:
    def __init__(self, content=b""):
        self.content = content

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.mounted = []
        self.get_calls = []
        self.post_calls = []

    def mount(self, prefix, adapter):
        self.mounted.append((prefix, adapter))

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeResponse(b"attachment-content")

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return FakeResponse(b"<html><table></table></html>")


class ScraperTest(unittest.TestCase):
    def _portal(self):
        logger = logging.getLogger(f"test.{self.id()}")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return ZvgPortal(logger, "test-agent", "https://example.test")

    def test_parse_details_downloads_attachment_for_any_land(self):
        portal = self._portal()
        fake_session = FakeSession()
        portal._session = fake_session
        entry = ObjektEntry(land_short="by", raw_list_sha256="raw-list")
        html = b"""
            <html><body>
                <a href="?button=showAnhang&land_abk=by&file_id=12&zvg_id=345">PDF</a>
                <table><tr><td>Aktenzeichen:</td><td>0001 K 0001/2024</td></tr></table>
            </body></html>
        """

        parsed = list(portal._parse_details(entry, html))

        self.assertIsInstance(parsed[0], RawAnhang)
        self.assertIs(parsed[-1], entry)
        self.assertEqual([parsed[0].sha256], entry.anhang_sha256s)
        self.assertEqual(1, len(fake_session.get_calls))
        self.assertIn("land_abk=by", fake_session.get_calls[0][0])

    def test_parse_details_without_table_returns_entry(self):
        portal = self._portal()
        entry = ObjektEntry(land_short="by", raw_list_sha256="raw-list")

        parsed = list(portal._parse_details(entry, b"<html><body>No table</body></html>"))

        self.assertEqual([entry], parsed)

    def test_list_bounds_detail_fetches_in_flight(self):
        """list() must not submit a whole Bundesland to the pool at once.

        The original code did `futures = [executor.submit(...) for e in
        entries_to_fetch]` and consumed them with `as_completed(futures)`.
        Two things then kept raw bytes alive far longer than necessary:

        1. the workers ran ahead of whoever consumes this generator, so
           finished results piled up inside their Futures, and
        2. the `futures` list held every Future - and so every detail page
           plus every attachment PDF - until the Bundesland was finished.

        With Nordrhein-Westfalen's ~1000 entries that was the bulk of a run's
        RSS, peaking near 1.8 GB. In Sep 2026 that peak was misread as a leak
        and "fixed" with a 1 GB container memory cap, which turned it into a
        crash loop: the process died at exactly the cap every ~90 s, 1278
        times over 32 h, and no scrape ever completed. It looked healthy the
        whole time - the failed allocation surfaced as a clean exit 0 with no
        OOM counter, and `restart: unless-stopped` kept the container "Up".

        So this test asserts the property that prevents all of that: work is
        submitted in a bounded window, while every entry is still delivered.
        """
        row_count = 20
        rows = "".join(
            f"<tr><td>Aktenzeichen:</td>"
            f'<td><a href="index.php?button=showZvg&zvg_id={i}&land_abk=by">{i}</a></td></tr>'
            for i in range(1, row_count + 1)
        )
        list_html = f"<html><body><table>{rows}</table></body></html>".encode("utf-8")
        detail_html = (
            b"\n<!DOCTYPE html><html><body><table>"
            b"<tr><td>Aktenzeichen:</td><td>0001 K 0001/2024</td></tr>"
            b"</table></body></html>"
        )

        class DetailSession(FakeSession):
            def get(self, url, **kwargs):
                self.get_calls.append((url, kwargs))
                return FakeResponse(detail_html)

            def post(self, url, **kwargs):
                self.post_calls.append((url, kwargs))
                return FakeResponse(list_html)

        created = []

        class RecordingExecutor(ThreadPoolExecutor):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.submitted = 0
                created.append(self)

            def submit(self, fn, *args, **kwargs):
                self.submitted += 1
                return super().submit(fn, *args, **kwargs)

        portal = self._portal()
        portal._session = DetailSession()
        portal._max_workers = 1  # window is max_workers * 2

        with mock.patch.object(scraper_module, "ThreadPoolExecutor", RecordingExecutor):
            produced = portal.list(Land(short="by", name="Bayern"))
            self.assertIsInstance(next(produced), RawList)
            next(produced)  # first detail payload: the pool is now running

            self.assertLessEqual(
                created[0].submitted,
                2,
                "detail fetches must be submitted in a bounded window, not all at once",
            )

            rest = list(produced)

        entries = [item for item in rest if isinstance(item, ObjektEntry)]
        self.assertEqual(row_count, len(entries), "every entry must still be delivered")
        self.assertEqual(row_count, created[0].submitted)

    def test_list_uses_configured_session(self):
        portal = self._portal()
        fake_session = FakeSession()
        portal._session = fake_session

        parsed = list(portal.list(Land(short="be", name="Berlin"), plz="10115"))

        self.assertEqual(1, len(fake_session.post_calls))
        self.assertIsInstance(parsed[0], RawList)

    def test_list_reports_detail_failure_and_delivers_remaining_entries(self):
        """One broken detail page must not swallow the rest of the Bundesland.

        Before this, a failing detail fetch only logged a warning and
        `continue`d: the entry vanished, and a caller had no way to tell which
        zvg_id was missing or why.
        """
        row_count = 6
        failing_zvg_id = 3
        list_html = _list_html(row_count)
        detail_html = _detail_html()

        class FailingDetailSession(FakeSession):
            def get(self, url, **kwargs):
                self.get_calls.append((url, kwargs))
                if f"zvg_id={failing_zvg_id}&" in url:
                    raise requests.HTTPError("500 Server Error: Internal Server Error")
                return FakeResponse(detail_html)

            def post(self, url, **kwargs):
                self.post_calls.append((url, kwargs))
                return FakeResponse(list_html)

        portal = self._portal()
        portal._session = FailingDetailSession()

        produced = list(portal.list(Land(short="by", name="Bayern")))

        self.assertIsInstance(produced[0], RawList)
        failures = [item for item in produced if isinstance(item, DetailFetchFailure)]
        entries = [item for item in produced if isinstance(item, ObjektEntry)]
        self.assertEqual(row_count - 1, len(entries), "every healthy entry must still be delivered")
        self.assertEqual(
            row_count - 1,
            len([item for item in produced if isinstance(item, RawEntry)]),
            "a failed row must not contribute a RawEntry payload",
        )
        self.assertEqual(1, len(failures))
        self.assertEqual(
            {failing_zvg_id},
            {f.zvg_id for f in failures},
            "the failure record must identify the missing zvg_id",
        )
        failure = failures[0]
        self.assertEqual("by", failure.land_short)
        self.assertIn("HTTPError", failure.reason)
        self.assertIn("500", failure.reason)
        self.assertIn(f"zvg_id={failing_zvg_id}", failure.url)
        # No partial payloads for the failed row: it must not look like a
        # successful entry to consumers.
        self.assertNotIn(failing_zvg_id, {e.zvg_id for e in entries})
        # The list page is still delivered before any detail payload.
        self.assertIsInstance(produced[0], RawList)
        self.assertNotIsInstance(produced[0], DetailFetchFailure)

    def test_list_detail_failure_record_is_distinguishable_per_entry(self):
        """Each failure carries its own identity, even when several fail."""
        row_count = 4
        failing = {2, 4}
        list_html = _list_html(row_count)
        detail_html = _detail_html()

        class PartlyFailingSession(FakeSession):
            def get(self, url, **kwargs):
                self.get_calls.append((url, kwargs))
                zvg_id = int(re.search(r"zvg_id=(\d+)", url).group(1))
                if zvg_id in failing:
                    return FakeResponse(b"<html>session expired</html>")
                return FakeResponse(detail_html)

            def post(self, url, **kwargs):
                self.post_calls.append((url, kwargs))
                return FakeResponse(list_html)

        portal = self._portal()
        portal._session = PartlyFailingSession()

        produced = list(portal.list(Land(short="by", name="Bayern")))

        failures = [item for item in produced if isinstance(item, DetailFetchFailure)]
        entries = [item for item in produced if isinstance(item, ObjektEntry)]
        self.assertEqual(failing, {f.zvg_id for f in failures})
        self.assertEqual(row_count - len(failing), len(entries))
        for failure in failures:
            self.assertIn("not a detail page", failure.reason)
        # Failures are not ObjektEntry subclasses, so consumers can split the
        # stream with a plain isinstance check.
        self.assertTrue(all(not isinstance(f, ObjektEntry) for f in failures))

    def test_list_without_failures_produces_no_failure_records(self):
        row_count = 5
        list_html = _list_html(row_count)
        detail_html = _detail_html()

        class HealthyDetailSession(FakeSession):
            def get(self, url, **kwargs):
                self.get_calls.append((url, kwargs))
                return FakeResponse(detail_html)

            def post(self, url, **kwargs):
                self.post_calls.append((url, kwargs))
                return FakeResponse(list_html)

        portal = self._portal()
        portal._session = HealthyDetailSession()

        produced = list(portal.list(Land(short="by", name="Bayern")))

        self.assertEqual([], [item for item in produced if isinstance(item, DetailFetchFailure)])
        self.assertEqual(row_count, len([item for item in produced if isinstance(item, ObjektEntry)]))

    def test_list_reports_parser_exception_as_failure_record(self):
        """A valid-looking page that blows up while parsing also yields a record."""
        row_count = 3
        failing_zvg_id = 2
        list_html = _list_html(row_count)
        detail_html = _detail_html()

        class DetailSession(FakeSession):
            def get(self, url, **kwargs):
                self.get_calls.append((url, kwargs))
                return FakeResponse(detail_html)

            def post(self, url, **kwargs):
                self.post_calls.append((url, kwargs))
                return FakeResponse(list_html)

        portal = self._portal()
        portal._session = DetailSession()
        real_parse_details = portal._parse_details

        def flaky_parse_details(entry, content):
            if entry.zvg_id == failing_zvg_id:
                raise ValueError("unexpected detail layout")
            return real_parse_details(entry, content)

        portal._parse_details = flaky_parse_details

        produced = list(portal.list(Land(short="by", name="Bayern")))

        failures = [item for item in produced if isinstance(item, DetailFetchFailure)]
        self.assertEqual([failing_zvg_id], [f.zvg_id for f in failures])
        self.assertIn("ValueError", failures[0].reason)
        self.assertIn("unexpected detail layout", failures[0].reason)
        self.assertEqual(
            row_count - 1,
            len([item for item in produced if isinstance(item, ObjektEntry)]),
            "parsing one bad page must not stop the others",
        )

    def test_list_keeps_bounded_window_when_detail_fetches_fail(self):
        """Failures must not degrade the bounded window into one big submit."""
        row_count = 20
        list_html = _list_html(row_count)
        detail_html = _detail_html()

        class FailingDetailSession(FakeSession):
            def get(self, url, **kwargs):
                self.get_calls.append((url, kwargs))
                raise requests.ConnectionError("connection reset")

            def post(self, url, **kwargs):
                self.post_calls.append((url, kwargs))
                return FakeResponse(list_html)

        created = []

        class RecordingExecutor(ThreadPoolExecutor):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.submitted = 0
                created.append(self)

            def submit(self, fn, *args, **kwargs):
                self.submitted += 1
                return super().submit(fn, *args, **kwargs)

        portal = self._portal()
        portal._session = FailingDetailSession()
        portal._max_workers = 1  # window is max_workers * 2

        with mock.patch.object(scraper_module, "ThreadPoolExecutor", RecordingExecutor):
            produced = portal.list(Land(short="by", name="Bayern"))
            self.assertIsInstance(next(produced), RawList)
            first_result = next(produced)  # first detail result: the pool is now running

            self.assertLessEqual(
                created[0].submitted,
                2,
                "detail fetches must stay in a bounded window even when they fail",
            )

            rest = list(produced)

        failures = [item for item in [first_result, *rest] if isinstance(item, DetailFetchFailure)]
        self.assertEqual(row_count, len(failures), "every row must end in a failure record")
        self.assertEqual(row_count, created[0].submitted)


def _list_html(row_count):
    rows = "".join(
        f"<tr><td>Aktenzeichen:</td>" f'<td><a href="index.php?button=showZvg&zvg_id={i}&land_abk=by">{i}</a></td></tr>'
        for i in range(1, row_count + 1)
    )
    return f"<html><body><table>{rows}</table></body></html>".encode("utf-8")


def _detail_html():
    return (
        b"\n<!DOCTYPE html><html><body><table>"
        b"<tr><td>Aktenzeichen:</td><td>0001 K 0001/2024</td></tr>"
        b"</table></body></html>"
    )


if __name__ == "__main__":
    unittest.main()
