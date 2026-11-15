#!/usr/bin/env python3
"""End-to-end check for the three-state X-Internal-Links classification.

Builds a local fake origin that emits four kinds of responses:

  * complete   - header + Count, no Truncated flag
  * truncated  - header + Truncated: 1 + Original > Count
  * missing    - no X-Internal-Links header at all
  * bad base64 - undecodable X-Internal-Links payload

Then it runs scripts/seo_header_probe.py against that origin and
scripts/seo_insights.py over the resulting summary, asserting:

  * per-URL status in the CSV, the summary JSON, and the aggregate counts agree
  * truncated pages never land in the dead-end list
  * missing / bad-base64 pages land in recrawl-list.txt
  * truncated pages keep their emitted links in the graph
  * a complete page with zero links is still reported as a real dead end

No network access is required. Run with:

    python3 scripts/test_link_status.py
"""
from __future__ import annotations

import base64
import csv
import json
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import seo_header_probe as probe  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  PASS {message}")
    else:
        print(f"  FAIL {message}")
        FAILURES.append(message)


def encode_links(links: list[str]) -> str:
    raw = json.dumps(links).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


# (path, kind, payload links, original count)
ROUTES: dict[str, dict] = {
    "/": {
        "kind": "complete",
        "links": [
            "/complete",
            "/truncated",
            "/truncated-empty",
            "/missing",
            "/bad-base64",
            "/leaf",
        ],
    },
    "/complete": {"kind": "complete", "links": ["/leaf"]},
    # A genuinely empty, complete list: must still be a dead end.
    "/leaf": {"kind": "complete", "links": []},
    # Discovered only from the truncated page; proves emitted links build the graph.
    "/t-only": {"kind": "complete", "links": []},
    # Truncated, keeps one real link (/t-only) plus a self link.
    "/truncated": {
        "kind": "truncated",
        "links": ["/t-only", "/truncated"],
        "original": 9,
    },
    # Truncated and decodes to only a self link -> 0 out-edges, but must not be
    # reported as a dead end because its link list is not complete.
    "/truncated-empty": {
        "kind": "truncated",
        "links": ["/truncated-empty"],
        "original": 7,
    },
    "/missing": {"kind": "missing"},
    "/bad-base64": {"kind": "bad"},
}


class QuietServer(ThreadingHTTPServer):
    """Same as ThreadingHTTPServer, but connection resets from clients are not errors."""

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        pass


class FakeOrigin(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        route = ROUTES.get(self.path.split("?", 1)[0])
        if route is None:
            route = {"kind": "complete", "links": []}

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", "0")

        kind = route["kind"]
        if kind == "complete":
            links = route["links"]
            self.send_header("X-Internal-Links", encode_links(links))
            self.send_header("X-Internal-Links-Encoding", "json+base64url")
            self.send_header("X-Internal-Links-Count", str(len(links)))
        elif kind == "truncated":
            links = route["links"]
            self.send_header("X-Internal-Links", encode_links(links))
            self.send_header("X-Internal-Links-Encoding", "json+base64url")
            self.send_header("X-Internal-Links-Count", str(len(links)))
            self.send_header("X-Internal-Links-Truncated", "1")
            self.send_header("X-Internal-Links-Original", str(route["original"]))
        elif kind == "bad":
            self.send_header("X-Internal-Links", "%%%not-valid-base64%%%")
            self.send_header("X-Internal-Links-Encoding", "json+base64url")
        # kind == "missing": emit no X-Internal-Links headers at all.

        self.end_headers()

    def log_message(self, *_args) -> None:  # silence request logging
        pass


# ---------- unit checks on the classifier -----------------------------------

def unit_checks() -> None:
    print("[unit] classify_links_status() on fabricated headers")

    def classify(header: str | None, companions: dict[str, str] | None = None) -> probe.FetchResult:
        result = probe.FetchResult(url="https://x/", path="/", status=200, elapsed_ms=0.0)
        if header is not None:
            result.headers[probe.HEADER_NAME] = header
        for key, value in (companions or {}).items():
            result.headers[key] = value
        if header is not None:
            result.decoded, result.decoded_error = probe.decode_header(header)
        elif result.error is None:
            result.decoded_error = "missing_header"
        probe.classify_links_status(result)
        return result

    complete = classify(
        encode_links(["/a", "/b"]),
        {probe.COUNT_HEADER_NAME: "2"},
    )
    check(complete.links_status == probe.LINKS_STATUS_COMPLETE, "complete payload -> complete")
    check(complete.links_decoded_count == 2, "complete payload decoded link count")

    flagged = classify(
        encode_links(["/a"]),
        {
            probe.TRUNCATED_HEADER_NAME: "1",
            probe.ORIGINAL_HEADER_NAME: "5",
            probe.COUNT_HEADER_NAME: "1",
        },
    )
    check(flagged.links_status == probe.LINKS_STATUS_TRUNCATED, "Truncated: 1 -> truncated")
    check(flagged.truncated_reason == "truncated_flag", "truncation reason recorded")

    inferred = classify(
        encode_links(["/a"]),
        {probe.ORIGINAL_HEADER_NAME: "4", probe.COUNT_HEADER_NAME: "1"},
    )
    check(inferred.links_status == probe.LINKS_STATUS_TRUNCATED,
          "Count < Original without flag -> truncated")

    short = classify(
        encode_links(["/a"]),
        {probe.COUNT_HEADER_NAME: "3"},
    )
    check(short.links_status == probe.LINKS_STATUS_TRUNCATED,
          "decoded list shorter than Count -> truncated")

    missing = classify(None)
    check(missing.links_status == probe.LINKS_STATUS_MISSING, "absent header -> missing")
    check(missing.links_status_reason == "missing_header", "missing reason is missing_header")

    bad = classify("%%%not-valid-base64%%%")
    check(bad.links_status == probe.LINKS_STATUS_MISSING, "bad base64 -> missing")
    check(
        (bad.links_status_reason or "").startswith("decode_error:"),
        "bad base64 reason is decode_error:*",
    )

    omitted = classify(None, {probe.COUNT_HEADER_NAME: "0", probe.ORIGINAL_HEADER_NAME: "4"})
    check(omitted.links_status == probe.LINKS_STATUS_MISSING,
          "header omitted after truncation -> missing")
    check(omitted.links_status_reason == "header_omitted_after_truncation",
          "omitted-header reason recorded")


# ---------- end-to-end checks ------------------------------------------------

def run_probe(base_url: str, out_dir: Path, probe_script: str = "seo_header_probe.py") -> None:
    if probe_script == "seo_header_probe.py":
        cmd = [
            sys.executable, str(HERE / probe_script),
            "--base-url", base_url,
            "--requests", "20",
            "--concurrency", "4",
            "--timeout", "5",
            "--max-attempts", "1",
            "--progress-interval", "1",
            "--out-dir", str(out_dir),
        ]
    else:
        cmd = [
            sys.executable, str(HERE / probe_script),
            "--base-url", base_url,
            "--requests", "20",
            "--concurrency", "4",
            "--timeout", "5",
            "--max-attempts", "1",
            "--progress-interval", "1",
            "--max-connections", "4",
            "--out-dir", str(out_dir),
        ]
    subprocess.run(cmd, cwd=REPO, check=True, stdout=subprocess.DEVNULL)


def run_insights(summary_path: Path, out_dir: Path) -> None:
    subprocess.run(
        [
            sys.executable, str(HERE / "seo_insights.py"),
            "--input", str(summary_path),
            "--out-dir", str(out_dir),
        ],
        cwd=REPO,
        check=True,
        stdout=subprocess.DEVNULL,
    )


EXPECTED = {
    "/": "complete",
    "/complete": "complete",
    "/leaf": "complete",
    "/t-only": "complete",
    "/truncated": "truncated",
    "/truncated-empty": "truncated",
    "/missing": "missing",
    "/bad-base64": "missing",
}
WANT_COUNTS = {"complete": 4, "truncated": 2, "missing": 2}


def collector_checks(label: str, probe_out: Path, base_url: str) -> None:
    print(f"[e2e] per-URL statuses in seo-header-summary.json ({label})")
    summary = json.loads((probe_out / "seo-header-summary.json").read_text(encoding="utf-8"))
    by_url = {entry["url"]: entry for entry in summary["urls"]}
    url_of = lambda path: f"{base_url}{path}"  # noqa: E731

    for path, status in EXPECTED.items():
        entry = by_url.get(url_of(path))
        check(entry is not None, f"{label} {path} present in summary")
        if entry is not None:
            check(entry.get("linksStatus") == status,
                  f"{label} {path} status={entry.get('linksStatus')} (want {status})")

    counts = summary.get("linksStatusCounts", {})
    check(counts == WANT_COUNTS,
          f"{label} summary linksStatusCounts={counts} (want {WANT_COUNTS})")

    recomputed: dict[str, int] = {"complete": 0, "truncated": 0, "missing": 0}
    for entry in summary["urls"]:
        recomputed[entry["linksStatus"]] += 1
    check(recomputed == WANT_COUNTS, f"{label} aggregate counts match per-URL records")

    truncated_entry = by_url[url_of("/truncated")]
    check("/t-only" in truncated_entry.get("links", []),
          f"{label} truncated page keeps its decoded links")
    check(truncated_entry.get("linksTruncated") is True,
          f"{label} truncated page is explicitly identifiable")
    check(summary.get("linksOmittedByTruncation") == 13,
          f"{label} omitted links counted ({summary.get('linksOmittedByTruncation')}, want 13)")

    with (probe_out / "seo-header-report.csv").open(newline="", encoding="utf-8") as fh:
        rows = {row["url"]: row for row in csv.DictReader(fh)}
    for path, status in EXPECTED.items():
        row = rows.get(url_of(path))
        check(row is not None and row["links_status"] == status,
              f"{label} CSV {path} links_status={row['links_status'] if row else None} (want {status})")


def insights_checks(summary_path: Path, insights_out: Path) -> None:
    print("[e2e] insights over the same summary")
    run_insights(summary_path, insights_out)
    report = json.loads((insights_out / "seo-insights.json").read_text(encoding="utf-8"))
    md = (insights_out / "seo-insights.md").read_text(encoding="utf-8")

    health_counts = report["crawlHealth"]["linksStatusCounts"]
    check(health_counts == WANT_COUNTS,
          f"insights linksStatusCounts={health_counts} (want {WANT_COUNTS})")

    dead_ends = set(report["deadEnds"]["examples"])
    check("/leaf" in dead_ends, "complete zero-link page stays a dead end")
    check("/truncated-empty" not in dead_ends, "truncated zero-link page not a dead end")
    check("/missing" not in dead_ends, "missing-header page not a dead end")
    check("/bad-base64" not in dead_ends, "bad-base64 page not a dead end")
    check(report["crawlIncomplete"]["deadEndsMaskedByIncompleteData"] == 3,
          "three incomplete pages would have looked like dead ends")

    incomplete = report["crawlIncomplete"]
    check(incomplete["truncatedCount"] == 2,
          f"crawlIncomplete.truncatedCount={incomplete['truncatedCount']} (want 2)")
    check(incomplete["missingCount"] == 2,
          f"crawlIncomplete.missingCount={incomplete['missingCount']} (want 2)")
    check(incomplete["truncatedLinksOmitted"] == 13,
          "insights reports omitted link total")

    recrawl = {
        line.strip()
        for line in (insights_out / "recrawl-list.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    want_recrawl = {"/truncated", "/truncated-empty", "/missing", "/bad-base64"}
    check(recrawl == want_recrawl,
          f"recrawl-list.txt={sorted(recrawl)} (want {sorted(want_recrawl)})")

    check("Link-data completeness" in md and "Data-completeness caveat" in md,
          "markdown explains incomplete-data caveats")


def e2e_checks() -> None:
    server = QuietServer(("127.0.0.1", 0), FakeOrigin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"[e2e] fake origin on {base_url}")

    try:
        with tempfile.TemporaryDirectory(prefix="link-status-") as tmp:
            tmp_path = Path(tmp)

            raw_probe_out = tmp_path / "probe-raw"
            run_probe(base_url, raw_probe_out, "seo_header_probe.py")
            collector_checks("raw-socket", raw_probe_out, base_url)
            insights_checks(raw_probe_out / "seo-header-summary.json", tmp_path / "insights-raw")

            try:
                import httpx  # noqa: F401
            except ImportError:
                print("[e2e] httpx not installed; skipping seo_header_probe_fast.py checks")
            else:
                fast_probe_out = tmp_path / "probe-fast"
                run_probe(base_url, fast_probe_out, "seo_header_probe_fast.py")
                collector_checks("httpx-fast", fast_probe_out, base_url)
                insights_checks(fast_probe_out / "seo-header-summary.json", tmp_path / "insights-fast")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> int:
    unit_checks()
    e2e_checks()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s)")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("All link-status checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
