"""Ad-hoc edge case checks for the crawler (run manually)."""
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "Web_Crawler"))  # legacy `from crawler import *`

from crawler import crawl, Worker  # noqa: E402  legacy bare-module import

FIXTURE_DIR = REPO_ROOT / "tests" / "fixture_site"


def main():
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    base = "http://127.0.0.1:{}/".format(port)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    original_cwd = os.getcwd()
    workdir = TemporaryDirectory()
    try:
        os.chdir(workdir.name)

        # 1) duplicate start URLs: one CrawlResult per request survives in
        #    summary.results; the shared dedup set ensures only the first
        #    worker actually claims and fetches the site once.
        results = crawl(base, base)
        assert list(results) == [base], list(results)
        assert len(results.results) == 2, len(results.results)
        winner, loser = results.results
        assert (winner.success, winner.failed, winner.skipped_external) == (
            4, 1, 3
        ), winner
        assert (loser.success, loser.failed, loser.skipped_external) == (
            0, 0, 0
        ), loser
        # aggregate view
        assert (results.success, results.failed,
                results.skipped_external) == (4, 1, 3), results.stats()
        print("duplicate start URL OK:", results)

        # 2) invalid scheme start URL -> failure with readable reason
        results = crawl("mailto:nobody@example.com")
        res = results["mailto:nobody@example.com"]
        assert res.success == 0 and res.failed == 1, res
        assert res.failed_urls[0][1] == "invalid URL", res.failed_urls
        print("invalid start URL OK:", res)

        # 3) legacy Worker API still functional
        w = Worker(base)
        w.start()
        w.join(timeout=30)
        assert not w.is_alive(), "worker hung"
        assert w.result.success == 4 and w.result.failed == 1
        assert len(w.crawled) == 4
        print("legacy Worker OK:", w.result)

        # 4) output file contents: two full crawls appended into one
        #    workdir, still no duplicate lines within a single crawl and
        #    every failure carries a readable reason.
        log = Path("log.txt").read_text(encoding="utf-8").splitlines()
        assert len(log) == 8, log          # 4 pages x 2 crawls
        from collections import Counter
        assert all(count == 2 for count in Counter(log).values()), log
        errors = Path("error_log.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        # one 404 per real crawl: duplicate-URL crawl, invalid-scheme
        # crawl, standalone Worker crawl
        assert len(errors) == 3, errors
        assert all("HTTP 404" in line or "invalid URL" in line
                   for line in errors), errors
        dump = Path("dump.txt").read_text(encoding="utf-8")
        assert "INDEX_BODY_MARKER" in dump and "PAGE2_BODY_MARKER" in dump
        print("output files OK: log=%d lines, errors=%d lines"
              % (len(log), len(errors)))
    finally:
        os.chdir(original_cwd)
        workdir.cleanup()

    server.shutdown()
    server.server_close()
    print("ALL EDGE CHECKS PASSED")


if __name__ == "__main__":
    main()
