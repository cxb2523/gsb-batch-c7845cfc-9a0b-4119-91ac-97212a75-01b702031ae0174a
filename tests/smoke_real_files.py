"""Smoke test: drive collector against the real repo files with a stub MMS.

Temporary copies are used so the real subscriptions / progress files are
never modified. No network access and no credentials.
"""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, r"D:\workspace\11-16\22093\A\attempt-1\repo")

from auto_score.collector import (
    parse_last_download,
    process_subscriptions,
    build_progress_updates,
    write_last_download_signal,
)

REPO = r"D:\workspace\11-16\22093\A\attempt-1\repo"


class StubMMS:
    def __init__(self, mode):
        self.mode = mode
        self.file_paths = []

    def get_recent_sheets(self, user, last_sheetnum):
        if self.mode == "raise":
            raise RuntimeError("boom")
        if self.mode == "empty":
            return [], 0, []
        return (
            [f"https://www.mymusicsheet.com/{user}/999999"],
            999999,
            [f"{user} | new song"],
        )

    def download_sheets(self, links):
        return self.mode != "download_fail"


def run_case(mode, expect_progress):
    tmp = tempfile.mkdtemp()
    try:
        sub = os.path.join(tmp, "subscriptions")
        sig = os.path.join(tmp, "_last_download_signal")
        shutil.copy(os.path.join(REPO, "subscriptions"), sub)
        shutil.copy(os.path.join(REPO, "_last_download_signal"), sig)

        with open(sub, encoding="utf-8") as f:
            lines = f.readlines()
        with open(sig) as f:
            last = parse_last_download(f.readlines())

        before = dict(last)
        handler = StubMMS(mode)
        outcomes = process_subscriptions(lines, last, lambda name: handler)
        updates = build_progress_updates(outcomes, last)
        write_last_download_signal(sig, updates)
        with open(sig) as f:
            after = parse_last_download(f.readlines())

        print(f"--- mode={mode} ---")
        print("status  :", [o.status for o in outcomes])
        print("before  :", before)
        print("after   :", after)
        assert after == expect_progress, (after, expect_progress)
        # the single real key must always remain present and never be 0
        key = next(iter(before))
        assert key in after and after[key] != 0
        print("OK\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


existing_key = next(
    iter(parse_last_download(
        open(os.path.join(REPO, "_last_download_signal")).readlines()
    ))
)

run_case("success", {existing_key: 999999.0})
run_case("raise", {existing_key: 532526.0})       # old value preserved
run_case("download_fail", {existing_key: 532526.0})
run_case("empty", {existing_key: 532526.0})
print("All smoke cases passed; real repo files untouched.")
