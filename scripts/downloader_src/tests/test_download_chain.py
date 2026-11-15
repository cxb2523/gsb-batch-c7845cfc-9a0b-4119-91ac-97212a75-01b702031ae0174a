"""Local branch tests for the multi-source download chain.

These use fabricated app configs plus injected downloader/fake source modules --
they exercise the *branch logic* only. They do not represent a real run against
APKMirror/APKPure/Uptodown/Aptoide, and passing here is not evidence that the
production network chain works.

Run from the repository root:

    python -m unittest discover -s scripts/downloader_src/tests -t scripts/downloader_src/tests -v
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# The package is deployed as ``src`` (folder renamed on install) and its modules
# do ``from src import ...``. Alias both the package and each already-imported
# submodule so tests and production share a single class identity per module
# instead of loading every module twice under two names.
import downloader_src  # noqa: E402

sys.modules.setdefault("src", downloader_src)
for _sub in ("utils", "outcomes", "apkmirror", "apkpure", "uptodown", "aptoide"):
    sys.modules.setdefault(f"src.{_sub}", importlib.import_module(f"downloader_src.{_sub}"))

from downloader_src import downloader  # noqa: E402
from downloader_src.outcomes import (  # noqa: E402
    OutcomeStatus,
    SourceFetchError,
    SourceOutcome,
    VersionNotFoundError,
)


class FakeModule:
    """Stand-in for one mirror module."""

    def __init__(self, latest=None, link=None, latest_exc=None, link_exc=None):
        self.latest = latest
        self.link = link
        self.latest_exc = latest_exc
        self.link_exc = link_exc
        self.latest_calls = []
        self.link_calls = []

    def get_latest_version(self, app_name, config):
        self.latest_calls.append((app_name, dict(config)))
        if self.latest_exc:
            raise self.latest_exc
        return self.latest

    def get_download_link(self, version, app_name, config):
        self.link_calls.append((version, app_name, dict(config)))
        if self.link_exc:
            raise self.link_exc
        return self.link


class FakeDownloader:
    def __init__(self, fail_substrings=()):
        self.fail_substrings = fail_substrings
        self.calls = []

    def __call__(self, url, name=None):
        self.calls.append(url)
        if any(s in url for s in self.fail_substrings):
            raise SourceFetchError(f"boom fetching {url}", stage="download")
        path = Path("fake-apk.apk")
        path.write_bytes(b"x")
        return path


class DownloadChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for platform in ("apkmirror", "apkpure", "uptodown", "aptoide"):
            (self.root / "apps" / platform).mkdir(parents=True)
            self._write_config(platform, {"package": "com.example.app", "name": "example"})

    def _write_config(self, platform, data):
        (self.root / "apps" / platform / "example.json").write_text(json.dumps(data))

    def _run(self, app_name="example", platforms=("apkmirror", "apkpure", "uptodown", "aptoide"),
             modules=None, downloader_func=None, supported_version=None):
        old = os.getcwd()
        os.chdir(self.root)
        try:
            with mock.patch.object(downloader.utils, "get_supported_version",
                                   return_value=supported_version):
                return downloader.download_app(
                    app_name, "cli.jar", "patches.mpp",
                    platforms=platforms,
                    platform_modules=modules,
                    downloader_func=downloader_func or FakeDownloader(),
                )
        finally:
            os.chdir(old)

    def test_pinned_version_is_used_for_every_source(self):
        self._write_config("apkmirror", {"package": "com.example.app", "name": "example", "version": "1.2.3"})
        self._write_config("apkpure", {"package": "com.example.app", "name": "example", "version": "1.2.3"})
        for platform in ("apkmirror", "apkpure"):
            module = FakeModule(latest="9.9.9", link=f"http://{platform}/1")
            report = self._run(platforms=(platform,), modules={platform: module},
                               supported_version="5.0.0")
            self.assertTrue(report.outcomes[0].ok, platform)
            # the pinned config version wins over both patch support and latest
            self.assertEqual(module.latest_calls, [], platform)
            self.assertEqual(module.link_calls[0][0], "1.2.3", platform)
            self.assertEqual(report.version, "1.2.3")
        self.assertEqual(report.apk, Path("fake-apk.apk"))

    def test_unpinned_prefers_patch_supported_version_over_source_latest(self):
        modules = {"apkpure": FakeModule(latest="7.0.0", link="http://pure/7")}
        report = self._run(platforms=("apkpure",), modules=modules, supported_version="6.5.0")
        self.assertEqual(report.version, "6.5.0")
        self.assertEqual(modules["apkpure"].link_calls[0][0], "6.5.0")
        self.assertEqual(modules["apkpure"].latest_calls, [])

    def test_unpinned_falls_back_to_source_latest(self):
        modules = {"apkpure": FakeModule(latest="7.0.0", link="http://pure/7")}
        report = self._run(platforms=("apkpure",), modules=modules, supported_version=None)
        self.assertEqual(report.version, "7.0.0")
        self.assertEqual(modules["apkpure"].latest_calls[0][0], "example")

    def test_source_that_raises_does_not_abort_the_round(self):
        modules = {
            "apkmirror": FakeModule(latest_exc=RuntimeError("kaboom")),
            "apkpure": FakeModule(latest_exc=SourceFetchError("pure down", stage="latest_version")),
            "uptodown": FakeModule(latest="2.0.0", link="http://up/2"),
        }
        dl = FakeDownloader()
        report = self._run(platforms=("apkmirror", "apkpure", "uptodown"), modules=modules,
                           downloader_func=dl, supported_version=None)
        self.assertEqual([o.status for o in report.outcomes],
                         [OutcomeStatus.ERROR, OutcomeStatus.ERROR, OutcomeStatus.SUCCESS])
        self.assertEqual(report.outcomes[0].stage, "latest_version")
        self.assertEqual(report.outcomes[1].stage, "latest_version")
        self.assertEqual(report.outcomes[0].detail, "kaboom")
        self.assertTrue(report.apk)
        self.assertEqual(dl.calls, ["http://up/2"])

    def test_not_found_and_error_are_distinguished(self):
        modules = {
            "apkmirror": FakeModule(latest_exc=VersionNotFoundError("no such app", stage="latest_version")),
            "apkpure": FakeModule(latest_exc=SourceFetchError("timeout", stage="latest_version")),
            "uptodown": FakeModule(latest=None),
            "aptoide": FakeModule(latest_exc=KeyError("vername")),
        }
        report = self._run(modules=modules, supported_version=None)
        self.assertEqual([o.status for o in report.outcomes], [
            OutcomeStatus.NOT_FOUND, OutcomeStatus.ERROR,
            OutcomeStatus.NOT_FOUND, OutcomeStatus.ERROR,
        ])
        self.assertTrue(report.all_failed)
        self.assertIsNone(report.apk)
        # every attempted source has an attributed outcome
        self.assertEqual([o.platform for o in report.outcomes],
                         ["apkmirror", "apkpure", "uptodown", "aptoide"])

    def test_download_failure_is_attributed_to_its_source(self):
        modules = {
            "apkmirror": FakeModule(latest="1.0", link="http://mirror/1"),
            "apkpure": FakeModule(latest="1.0", link="http://pure/1"),
        }
        dl = FakeDownloader(fail_substrings=("mirror",))
        report = self._run(platforms=("apkmirror", "apkpure"), modules=modules,
                           downloader_func=dl, supported_version=None)
        first, second = report.outcomes
        self.assertEqual(first.status, OutcomeStatus.ERROR)
        self.assertEqual(first.stage, "download")
        self.assertEqual(first.platform, "apkmirror")
        self.assertEqual(second.status, OutcomeStatus.SUCCESS)
        self.assertEqual(second.platform, "apkpure")
        self.assertEqual(second.version, "1.0")

    def test_link_fetch_failure_is_not_counted_as_not_found(self):
        modules = {"apkmirror": FakeModule(latest="1.0", link_exc=SourceFetchError("502", stage="download_link"))}
        report = self._run(platforms=("apkmirror",), modules=modules, supported_version=None)
        self.assertEqual(report.outcomes[0].status, OutcomeStatus.ERROR)
        self.assertEqual(report.outcomes[0].stage, "download_link")

    def test_missing_config_leaves_outcome_and_continues(self):
        (self.root / "apps" / "apkmirror" / "example.json").unlink()
        modules = {
            "apkmirror": FakeModule(latest="3.0", link="http://mirror/3"),
            "apkpure": FakeModule(latest="3.0", link="http://pure/3"),
        }
        report = self._run(platforms=("apkmirror", "apkpure"), modules=modules,
                           supported_version=None)
        self.assertEqual(report.outcomes[0].status, OutcomeStatus.ERROR)
        self.assertEqual(report.outcomes[0].stage, "config")
        self.assertEqual(report.outcomes[1].status, OutcomeStatus.SUCCESS)

    def test_unknown_platform_is_an_error_outcome(self):
        report = self._run(platforms=("nosuch", "apkpure"),
                           modules={"apkpure": FakeModule(latest="1.0", link="http://pure/1")},
                           supported_version=None)
        self.assertEqual(report.outcomes[0].status, OutcomeStatus.ERROR)
        self.assertEqual(report.outcomes[0].stage, "platform")
        self.assertEqual(report.outcomes[1].status, OutcomeStatus.SUCCESS)

    def test_outcome_unpacks_like_the_old_tuple(self):
        outcome = SourceOutcome(platform="apkpure", status=OutcomeStatus.SUCCESS,
                                version="1.0", apk=Path("a.apk"))
        self.assertEqual(tuple(outcome), (Path("a.apk"), "1.0"))
        failed = SourceOutcome(platform="apkpure", status=OutcomeStatus.NOT_FOUND)
        self.assertEqual(tuple(failed), (None, None))

    def test_supported_version_query_failure_fails_that_source_only(self):
        """A broken CLI query is an error, not a silent switch to another version."""
        modules = {
            "apkmirror": FakeModule(latest="4.0", link="http://mirror/4"),
            "apkpure": FakeModule(latest="4.0", link="http://pure/4"),
        }
        old = os.getcwd()
        os.chdir(self.root)
        try:
            with mock.patch.object(downloader.utils, "get_supported_version", side_effect=SystemExit(1)):
                report = downloader.download_app(
                    "example", "cli.jar", "patches.mpp",
                    platforms=("apkmirror", "apkpure"),
                    platform_modules=modules,
                    downloader_func=FakeDownloader(),
                )
        finally:
            os.chdir(old)
        # No silent version substitution: the source fails, the round continues.
        self.assertEqual(report.outcomes[0].status, OutcomeStatus.ERROR)
        self.assertEqual(report.outcomes[0].stage, "supported_version")
        self.assertEqual(modules["apkmirror"].latest_calls, [])
        self.assertEqual(report.outcomes[1].status, OutcomeStatus.ERROR)
        self.assertEqual(len(report.outcomes), 2)

    def test_stops_after_first_success(self):
        modules = {
            "apkmirror": FakeModule(latest="1.0", link="http://mirror/1"),
            "apkpure": FakeModule(latest="1.0", link="http://pure/1"),
            "uptodown": FakeModule(latest="1.0", link="http://up/1"),
        }
        dl = FakeDownloader()
        report = self._run(modules=modules, downloader_func=dl, supported_version=None)
        self.assertEqual(len(report.outcomes), 1)
        self.assertEqual(dl.calls, ["http://mirror/1"])

    def test_report_serialises_for_build_summary(self):
        modules = {
            "apkmirror": FakeModule(latest_exc=SourceFetchError("500", stage="latest_version")),
            "apkpure": FakeModule(latest="1.0", link="http://pure/1"),
        }
        report = self._run(platforms=("apkmirror", "apkpure"), modules=modules,
                           supported_version=None)
        data = report.as_dict()
        self.assertEqual(data["success"], "apkpure")
        self.assertEqual([s["status"] for s in data["sources"]], ["error", "success"])
        self.assertIn("apkmirror: error", report.format_summary())


class UptodownPaginationTests(unittest.TestCase):
    """The real pagination helper treats fetch failures as source errors."""

    CFG = {"name": "example", "package": "com.example.app"}

    def test_fetch_error_becomes_source_error_not_silent_none(self):
        from downloader_src import uptodown
        with mock.patch.object(uptodown, "session") as sess:
            sess.get.side_effect = ConnectionError("dns fail")
            with self.assertRaises(SourceFetchError):
                uptodown.get_download_link("1.0", "example", self.CFG)

    def test_version_list_page_failure_becomes_source_error(self):
        from downloader_src import uptodown
        html_ok = mock.Mock(status_code=200,
                            content=b'<h1 id="detail-app-name" data-code="xyz"></h1>')
        html_ok.raise_for_status = lambda: None

        def fake_get(url, *a, **k):
            if url.endswith("/versions"):
                return html_ok
            raise ConnectionError("pagination endpoint 500")

        with mock.patch.object(uptodown, "session") as sess:
            sess.get.side_effect = fake_get
            with self.assertRaises(SourceFetchError):
                uptodown.get_download_link("1.0", "example", self.CFG)

    def test_reached_but_version_absent_is_not_found(self):
        from downloader_src import uptodown
        html_ok = mock.Mock(status_code=200,
                            content=b'<h1 id="detail-app-name" data-code="xyz"></h1>')
        html_ok.raise_for_status = lambda: None

        def page_json():
            r = mock.Mock(status_code=200)
            r.raise_for_status = lambda: None
            r.json.return_value = {"data": [
                {"version": "0.9", "versionURL": {"url": "u", "extraURL": "e", "versionID": "1"}},
            ]}
            return r

        def fake_get(url, *a, **k):
            if url.endswith("/versions"):
                return html_ok
            return page_json()

        with mock.patch.object(uptodown, "session") as sess:
            sess.get.side_effect = fake_get
            with self.assertRaises(VersionNotFoundError):
                uptodown.get_download_link("1.0", "example", self.CFG)


class ApkpureClassificationTests(unittest.TestCase):
    """HTTP 404 is absence; other HTTP failures are source errors."""

    CFG = {"name": "example", "package": "com.example.app"}

    def _http_error(self, status):
        import requests
        resp = requests.Response()
        resp.status_code = status
        return requests.HTTPError(f"{status} error", response=resp)

    def test_404_is_not_found(self):
        from downloader_src import apkpure
        with mock.patch.object(apkpure, "session") as sess:
            sess.get.return_value.raise_for_status.side_effect = self._http_error(404)
            with self.assertRaises(VersionNotFoundError):
                apkpure.get_download_link("1.0", "example", self.CFG)

    def test_503_is_source_error(self):
        from downloader_src import apkpure
        with mock.patch.object(apkpure, "session") as sess:
            sess.get.return_value.raise_for_status.side_effect = self._http_error(503)
            with self.assertRaises(SourceFetchError):
                apkpure.get_download_link("1.0", "example", self.CFG)


class AptoideQueryTests(unittest.TestCase):
    """Aptoide version queries surface network/parse failures as source errors."""

    def test_version_query_transport_failure_is_source_error(self):
        from downloader_src import aptoide
        with mock.patch.object(aptoide, "session") as sess:
            sess.get.side_effect = ConnectionError("aptoide down")
            with self.assertRaises(SourceFetchError) as ctx:
                aptoide.get_download_link("1.0", "example", {"package": "com.example.app"})
        self.assertEqual(ctx.exception.stage, "download_link")

    def test_absent_version_is_not_found(self):
        from downloader_src import aptoide
        response = mock.Mock()
        response.raise_for_status = lambda: None
        response.json.return_value = {"datalist": {"list": [
            {"file": {"vername": "0.9", "vercode": 9}},
        ]}}
        with mock.patch.object(aptoide, "session") as sess:
            sess.get.return_value = response
            with self.assertRaises(VersionNotFoundError):
                aptoide.get_download_link("1.0", "example", {"package": "com.example.app"})

    def test_malformed_response_is_source_error(self):
        from downloader_src import aptoide
        response = mock.Mock()
        response.raise_for_status = lambda: None
        response.json.return_value = {"unexpected": True}
        with mock.patch.object(aptoide, "session") as sess:
            sess.get.return_value = response
            with self.assertRaises(SourceFetchError):
                aptoide.get_download_link("1.0", "example", {"package": "com.example.app"})


if __name__ == "__main__":
    unittest.main()
