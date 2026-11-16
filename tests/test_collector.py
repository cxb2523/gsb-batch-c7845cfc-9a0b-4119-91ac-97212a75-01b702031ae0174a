"""Local, network-free tests for auto_score.collector.

The real MMS fetching (GraphQL) and PDF downloading are replaced by fake
handlers / stubs so nothing here performs a network call or needs
credentials.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from auto_score.collector import (  # noqa: E402
    EMPTY,
    FETCH_ERROR,
    DOWNLOAD_FAILED,
    SUCCESS,
    build_progress_updates,
    parse_last_download,
    process_subscriptions,
    subscription_md5,
    write_last_download_signal,
)


class FakeHandler:
    """In-memory stub replacing MMS fetching and PDF downloading."""

    def __init__(self, behavior, links=None, max_sheetnum=0, titles=None):
        # behavior: "ok" | "fetch_raises" | "download_fails"
        self.behavior = behavior
        self._links = links or []
        self._max_sheetnum = max_sheetnum
        self._titles = titles or []
        self.get_calls = []
        self.download_calls = []
        self.file_paths = []

    def get_recent_sheets(self, user, last_sheetnum):
        self.get_calls.append((user, last_sheetnum))
        if self.behavior == "fetch_raises":
            raise RuntimeError("simulated fetch failure")
        return list(self._links), self._max_sheetnum, list(self._titles)

    def download_sheets(self, links):
        self.download_calls.append(list(links))
        if self.behavior == "download_fails":
            return False
        return True


def make_resolver(handler_map):
    """Return a get_handler-style resolver for the given source map."""

    def resolver(source_name):
        return handler_map.get(source_name)

    return resolver


def md5_of(line):
    return subscription_md5(line)


class ProcessSubscriptionsTests(unittest.TestCase):
    def setUp(self):
        # three subscriptions; md5 keys are derived from the full raw lines
        self.lines = [
            "mms|authorA\n",
            "mms|authorB\n",
            "mms|authorC\n",
        ]
        self.key_a = md5_of("mms|authorA")
        self.key_b = md5_of("mms|authorB")
        self.key_c = md5_of("mms|authorC")
        self.errors = []

    def _run(self, handler_map, last_downloads=None):
        return process_subscriptions(
            self.lines,
            last_downloads or {},
            make_resolver(handler_map),
            error_collector=self.errors.append,
        )

    def test_all_success_advances_progress_for_every_author(self):
        handler_map = {
            "mms": FakeHandler(
                "ok",
                links=["https://www.mymusicsheet.com/x/1001"],
                max_sheetnum=1001,
                titles=["A | song"],
            )
        }
        last = {self.key_a: 900.0, self.key_b: 800.0, self.key_c: 700.0}
        outcomes = self._run(handler_map, last)

        # all three authors were processed
        self.assertEqual([o.status for o in outcomes], [SUCCESS, SUCCESS, SUCCESS])
        self.assertEqual([o.user for o in outcomes], ["authorA", "authorB", "authorC"])
        # resolver was consulted once per author and download was invoked
        self.assertEqual(len(outcomes), 3)
        for outcome in outcomes:
            self.assertEqual(outcome.sheet_count, 1)
            self.assertEqual(outcome.max_sheetnum, 1001)
        self.assertEqual(self.errors, [])

        updates = build_progress_updates(outcomes, last)
        self.assertEqual(
            updates,
            {self.key_a: 1001, self.key_b: 1001, self.key_c: 1001},
        )

    def test_fetch_exception_isolates_author_and_keeps_others_running(self):
        ok_handler = FakeHandler(
            "ok",
            links=["https://www.mymusicsheet.com/x/2002"],
            max_sheetnum=2002,
        )
        raising_handler = FakeHandler("fetch_raises")

        # source differs per line so each author can get its own handler
        lines = ["srcA|authorA\n", "srcB|authorB\n", "srcC|authorC\n"]
        handler_map = {
            "srcA": ok_handler,
            "srcB": raising_handler,
            "srcC": ok_handler,
        }
        key_a, key_b, key_c = (md5_of(l.strip()) for l in lines)
        last = {key_a: 100.0, key_b: 200.0, key_c: 300.0}

        outcomes = process_subscriptions(
            lines,
            last,
            make_resolver(handler_map),
            error_collector=self.errors.append,
        )

        statuses = {o.user: o.status for o in outcomes}
        self.assertEqual(statuses["authorA"], SUCCESS)
        self.assertEqual(statuses["authorB"], FETCH_ERROR)
        self.assertEqual(statuses["authorC"], SUCCESS)

        # the raising author did not stop later authors from being processed
        self.assertEqual(ok_handler.get_calls[0][0], "authorA")
        self.assertEqual(ok_handler.get_calls[1][0], "authorC")
        self.assertEqual(raising_handler.download_calls, [])

        # failure reason is surfaced through the error collector
        self.assertTrue(any("authorB" in e for e in self.errors))

        updates = build_progress_updates(outcomes, last)
        # successful authors advance; failed author keeps its old value
        self.assertEqual(updates[key_a], 2002)
        self.assertEqual(updates[key_c], 2002)
        self.assertEqual(updates[key_b], 200.0)
        self.assertNotEqual(updates[key_b], 0)

    def test_download_failure_keeps_old_progress_and_continues(self):
        good = FakeHandler(
            "ok",
            links=["https://www.mymusicsheet.com/x/3003"],
            max_sheetnum=3003,
        )
        bad_download = FakeHandler(
            "download_fails",
            links=["https://www.mymusicsheet.com/x/4004"],
            max_sheetnum=4004,
        )
        lines = ["srcA|authorA\n", "srcB|authorB\n", "srcC|authorC\n"]
        handler_map = {"srcA": good, "srcB": bad_download, "srcC": good}
        key_a, key_b, key_c = (md5_of(l.strip()) for l in lines)
        last = {key_a: 11.0, key_b: 22.0, key_c: 33.0}

        outcomes = process_subscriptions(
            lines,
            last,
            make_resolver(handler_map),
            error_collector=self.errors.append,
        )

        statuses = {o.user: o.status for o in outcomes}
        self.assertEqual(statuses["authorA"], SUCCESS)
        self.assertEqual(statuses["authorB"], DOWNLOAD_FAILED)
        self.assertEqual(statuses["authorC"], SUCCESS)

        # download was attempted for the failing author, and the good
        # author after it still ran
        self.assertEqual(len(bad_download.download_calls), 1)
        self.assertEqual(good.get_calls[-1][0], "authorC")
        self.assertTrue(any("authorB" in e for e in self.errors))

        updates = build_progress_updates(outcomes, last)
        self.assertEqual(updates[key_a], 3003)
        self.assertEqual(updates[key_c], 3003)
        # failed author must not be zeroed, deleted, or advanced
        self.assertEqual(updates[key_b], 22.0)

    def test_empty_result_preserves_old_progress_without_downloading(self):
        empty_handler = FakeHandler("ok", links=[], max_sheetnum=0)
        good_handler = FakeHandler(
            "ok",
            links=["https://www.mymusicsheet.com/x/5005"],
            max_sheetnum=5005,
        )
        lines = ["srcA|authorA\n", "srcB|authorB\n"]
        handler_map = {"srcA": empty_handler, "srcB": good_handler}
        key_a, key_b = (md5_of(l.strip()) for l in lines)
        last = {key_a: 55.0, key_b: 66.0}

        outcomes = process_subscriptions(
            lines,
            last,
            make_resolver(handler_map),
            error_collector=self.errors.append,
        )

        statuses = {o.user: o.status for o in outcomes}
        self.assertEqual(statuses["authorA"], EMPTY)
        self.assertEqual(statuses["authorB"], SUCCESS)
        # no download attempted when there is nothing new
        self.assertEqual(empty_handler.download_calls, [])
        self.assertEqual(self.errors, [])

        updates = build_progress_updates(outcomes, last)
        # empty result must not overwrite the old sheetId
        self.assertEqual(updates[key_a], 55.0)
        self.assertEqual(updates[key_b], 5005)

    def test_brand_new_failed_or_empty_subscription_is_not_written(self):
        raising = FakeHandler("fetch_raises")
        empty = FakeHandler("ok", links=[], max_sheetnum=0)
        lines = ["srcA|newFail\n", "srcB|newEmpty\n"]
        handler_map = {"srcA": raising, "srcB": empty}

        outcomes = process_subscriptions(
            lines,
            {},  # no prior progress at all
            make_resolver(handler_map),
            error_collector=self.errors.append,
        )
        updates = build_progress_updates(outcomes, {})
        # nothing may be persisted: especially never a 0 entry
        self.assertEqual(updates, {})

    def test_missing_handler_does_not_stop_remaining_authors(self):
        good = FakeHandler(
            "ok",
            links=["https://www.mymusicsheet.com/x/6006"],
            max_sheetnum=6006,
        )
        lines = ["unknown|authorA\n", "srcB|authorB\n"]
        handler_map = {"srcB": good}
        key_b = md5_of("srcB|authorB")

        outcomes = process_subscriptions(
            lines,
            {},
            make_resolver(handler_map),
            error_collector=self.errors.append,
        )
        self.assertEqual(outcomes[0].status, FETCH_ERROR)
        self.assertEqual(outcomes[1].status, SUCCESS)
        self.assertTrue(any("authorA" in e for e in self.errors))

        updates = build_progress_updates(outcomes, {key_b: 1.0})
        self.assertEqual(updates, {key_b: 6006})


class ProgressFileTests(unittest.TestCase):
    def test_round_trip_write_and_parse(self):
        lines = ["mms|authorA\n", "mms|authorB\n", "mms|authorC\n"]
        key_a, key_b, key_c = (md5_of(l.strip()) for l in lines)
        handler_map = {
            "mms": FakeHandler(
                "ok",
                links=["https://www.mymusicsheet.com/x/7007"],
                max_sheetnum=7007,
            )
        }
        last = {key_a: 1.0, key_b: 2.0, key_c: 3.0}
        outcomes = process_subscriptions(
            lines, last, make_resolver(handler_map)
        )
        updates = build_progress_updates(outcomes, last)

        with tempfile.TemporaryDirectory() as tmp:
            signal_path = os.path.join(tmp, "_last_download_signal")
            write_last_download_signal(signal_path, updates)
            with open(signal_path, "r") as f:
                written = f.read()

            reloaded = parse_last_download(written.splitlines(keepends=True))

        self.assertEqual(
            reloaded,
            {key_a: 7007.0, key_b: 7007.0, key_c: 7007.0},
        )

    def test_failed_author_value_survives_full_write_cycle(self):
        good = FakeHandler(
            "ok",
            links=["https://www.mymusicsheet.com/x/8008"],
            max_sheetnum=8008,
        )
        raising = FakeHandler("fetch_raises")
        lines = ["srcA|authorA\n", "srcB|authorB\n"]
        key_a, key_b = (md5_of(l.strip()) for l in lines)
        last = {key_a: 101.0, key_b: 202.0}

        outcomes = process_subscriptions(
            lines,
            last,
            make_resolver({"srcA": good, "srcB": raising}),
            error_collector=lambda msg: None,
        )
        updates = build_progress_updates(outcomes, last)

        with tempfile.TemporaryDirectory() as tmp:
            signal_path = os.path.join(tmp, "_last_download_signal")
            write_last_download_signal(signal_path, updates)
            with open(signal_path, "r") as f:
                reloaded = parse_last_download(f.readlines())

        self.assertEqual(reloaded[key_a], 8008.0)
        # failed author's previous progress is persisted unchanged and
        # there is no zero-valued record for it
        self.assertEqual(reloaded[key_b], 202.0)
        self.assertNotIn(0.0, reloaded.values())


if __name__ == "__main__":
    unittest.main()
