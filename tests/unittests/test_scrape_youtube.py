"""Offline tests for tools/scraping/scrape_youtube.py.

The real modules (pytube, youtube_transcript_api, google_ngram_downloader, selenium)
are replaced by lightweight fakes, so that no network access is required.
"""

import csv
import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest

SCRIPT_PATH = os.path.realpath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "tools", "scraping", "scrape_youtube.py"))

STUBBED_MODULES = ("pytube", "pytube.exceptions", "youtube_transcript_api", "google_ngram_downloader", "selenium", "selenium.webdriver")


class FakeTranscriptsDisabled(Exception):
    pass


class FakeVideoUnavailable(Exception):
    pass


class FakeYouTubeRequestFailed(Exception):
    pass


class FakeTranscript:
    def __init__(self, language_code, language, text="Bonjour le monde", fetch_error=None):
        self.language_code = language_code
        self.language = language
        self._text = text
        self._fetch_error = fetch_error

    def fetch(self):
        if self._fetch_error is not None:
            raise self._fetch_error
        return [{"text": self._text, "start": 0.0, "duration": 1.5}]


class FakeYouTubeTranscriptApi:
    """Behaviour is scripted per video id: a queue of outcomes consumed on every call."""

    # vid -> list of outcomes; an outcome is either an exception instance or a list of FakeTranscript
    OUTCOMES = {}
    CALLS = []

    @classmethod
    def reset(cls):
        cls.OUTCOMES = {}
        cls.CALLS = []

    @classmethod
    def script(cls, vid, outcomes):
        cls.OUTCOMES[vid] = list(outcomes)

    @classmethod
    def list_transcripts(cls, vid, proxies=None):
        cls.CALLS.append((vid, proxies))
        queue = cls.OUTCOMES.get(vid, [])
        outcome = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else [])
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def build_stub_modules():
    youtube_transcript_api = types.ModuleType("youtube_transcript_api")
    youtube_transcript_api.TranscriptsDisabled = FakeTranscriptsDisabled
    youtube_transcript_api.VideoUnavailable = FakeVideoUnavailable
    youtube_transcript_api.YouTubeRequestFailed = FakeYouTubeRequestFailed
    youtube_transcript_api.YouTubeTranscriptApi = FakeYouTubeTranscriptApi

    pytube = types.ModuleType("pytube")
    pytube_exceptions = types.ModuleType("pytube.exceptions")

    class FakeAgeRestrictedError(Exception):
        pass

    class FakeYouTube:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Network access is forbidden in this test")

    pytube.YouTube = FakeYouTube
    pytube_exceptions.AgeRestrictedError = FakeAgeRestrictedError
    pytube.exceptions = pytube_exceptions

    google_ngram_downloader = types.ModuleType("google_ngram_downloader")
    google_ngram_downloader.readline_google_store = lambda *args, **kwargs: iter(())

    selenium = types.ModuleType("selenium")
    selenium_webdriver = types.ModuleType("selenium.webdriver")
    selenium_webdriver.FirefoxOptions = object
    selenium_webdriver.Firefox = object
    selenium.webdriver = selenium_webdriver

    return {
        "youtube_transcript_api": youtube_transcript_api,
        "pytube": pytube,
        "pytube.exceptions": pytube_exceptions,
        "google_ngram_downloader": google_ngram_downloader,
        "selenium": selenium,
        "selenium.webdriver": selenium_webdriver,
    }


class TestScrapeYouTube(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_modules = {name: sys.modules.get(name) for name in STUBBED_MODULES}
        cls.stubs = build_stub_modules()
        sys.modules.update(cls.stubs)

        spec = importlib.util.spec_from_file_location("scrape_youtube_under_test", SCRIPT_PATH)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        for name, mod in cls._saved_modules.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="scrape_youtube_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.module.ALL_IDS.clear()
        self.module.CHECKED_PROXIES.clear()
        FakeYouTubeTranscriptApi.reset()

        # Never hit the network to test the proxies
        self._saved_check_proxy = self.module.check_proxy
        self.module.check_proxy = lambda proxy, **kwargs: True

        # Count writes to the discarded folder
        self.discarded_writes = []
        self._saved_register = self.module.register_discarded_id

        def counting_register(video_id, path, reason="", temporary=False):
            self.discarded_writes.append((video_id, str(reason), temporary))
            return self._saved_register(video_id, path, reason=reason, temporary=temporary)

        self.module.register_discarded_id = counting_register

    def tearDown(self):
        self.module.check_proxy = self._saved_check_proxy
        self.module.register_discarded_id = self._saved_register

    def discarded_path(self, vid):
        return os.path.join(self.tmpdir, "discarded", f"{vid}.txt")

    def has_subtitles(self, vid, lang="fr"):
        return os.path.isfile(os.path.join(self.tmpdir, lang, f"{vid}.csv"))

    def run_scrape(self, video_ids, proxies=("p1", "p2"), **kwargs):
        kwargs.setdefault("skip_if_exists", True)
        kwargs.setdefault("skip_if_discarded", True)
        return self.module.scrape_transcriptions(video_ids, self.tmpdir, "fr", proxies=list(proxies), verbose=False, **kwargs)

    def test_available_video_is_written_and_not_discarded(self):
        vid = "available001"
        FakeYouTubeTranscriptApi.script(vid, [[FakeTranscript("fr", "French", text="ligne 1\nligne 2")]])

        self.run_scrape([vid])

        csv_path = os.path.join(self.tmpdir, "fr", f"{vid}.csv")
        self.assertTrue(os.path.isfile(csv_path), "Subtitles should be written to disk")
        with open(csv_path, encoding="utf8") as f:
            rows = list(csv.reader(f, delimiter=";"))
        self.assertEqual(rows[0], ["text", "start", "duration"])
        self.assertEqual(rows[1][0], "ligne 1 ligne 2", "Newlines must be replaced by spaces")
        self.assertEqual(rows[1][1], "0.0")
        self.assertFalse(os.path.isfile(self.discarded_path(vid)), "A successful video must not be recorded as discarded")
        self.assertEqual(self.discarded_writes, [])

    def test_subtitles_disabled_is_discarded_once_without_other_proxies(self):
        vid = "disabled001"
        FakeYouTubeTranscriptApi.script(vid, [FakeTranscriptsDisabled("subtitles disabled")])

        self.run_scrape([vid])

        calls = [c for c in FakeYouTubeTranscriptApi.CALLS if c[0] == vid]
        self.assertEqual(len(calls), 1, "A definite unavailability must not be retried with other proxies")
        self.assertEqual(len(self.discarded_writes), 1, "Discarded reason must be written exactly once")
        self.assertFalse(self.discarded_writes[0][2], "A disabled-subtitles record is not a temporary failure")
        self.assertTrue(os.path.isfile(self.discarded_path(vid)))
        self.assertFalse(self.has_subtitles(vid), "No subtitles should be written for a discarded video")

        # A new run must not retry it either
        self.module.ALL_IDS.clear()
        FakeYouTubeTranscriptApi.CALLS = []
        self.run_scrape([vid])
        self.assertEqual([c for c in FakeYouTubeTranscriptApi.CALLS if c[0] == vid], [], "Permanently discarded videos must be skipped")

    def test_video_unavailable_is_discarded_permanently(self):
        vid = "missing0001"
        FakeYouTubeTranscriptApi.script(vid, [FakeVideoUnavailable("Video unavailable")])

        self.run_scrape([vid])

        self.assertEqual(len(self.discarded_writes), 1)
        self.assertFalse(self.discarded_writes[0][2])
        self.assertTrue(os.path.isfile(self.discarded_path(vid)))

    def test_temporary_failures_are_retried_then_marked_temporary(self):
        vid = "proxyfail01"
        FakeYouTubeTranscriptApi.script(
            vid,
            [
                self._proxy_error(),
                self._proxy_error(),
                self._proxy_error(),
            ],
        )

        self.run_scrape([vid])

        calls = [c for c in FakeYouTubeTranscriptApi.CALLS if c[0] == vid]
        self.assertEqual(len(calls), 3, "All proxies must be tried before giving up")
        self.assertEqual(len(self.discarded_writes), 1, "The failure must be recorded once")
        self.assertTrue(self.discarded_writes[0][2], "Proxy failures must be flagged as temporary")
        self.assertTrue(os.path.isfile(self.discarded_path(vid)))
        with open(self.discarded_path(vid), encoding="utf8") as f:
            reason = f.read()
        self.assertTrue(reason.startswith(self.module.ERROR_TEMPORARY), reason)
        self.assertIn(self.module.ERROR_PROXY, reason)

        # A later run (same process) must retry the video instead of skipping it as discarded
        self.module.CHECKED_PROXIES.clear()
        FakeYouTubeTranscriptApi.CALLS = []
        FakeYouTubeTranscriptApi.script(vid, [[FakeTranscript("fr", "French")]])
        self.run_scrape([vid])

        calls = [c for c in FakeYouTubeTranscriptApi.CALLS if c[0] == vid]
        self.assertGreaterEqual(len(calls), 1, "A temporary failure must be retried on the next run")
        self.assertTrue(self.has_subtitles(vid), "The retry should succeed and write subtitles")
        self.assertFalse(os.path.isfile(self.discarded_path(vid)), "The stale temporary record must be removed after success")

    def test_temporary_failure_is_retried_after_process_restart(self):
        vid = "proxyfail02"
        FakeYouTubeTranscriptApi.script(vid, [self._proxy_error()])

        self.run_scrape([vid])
        self.assertTrue(os.path.isfile(self.discarded_path(vid)))

        # Simulate a new process: fresh caches, then a successful run
        self.module.ALL_IDS.clear()
        FakeYouTubeTranscriptApi.CALLS = []
        FakeYouTubeTranscriptApi.script(vid, [[FakeTranscript("fr", "French")]])
        self.run_scrape([vid])

        self.assertGreaterEqual(len([c for c in FakeYouTubeTranscriptApi.CALLS if c[0] == vid]), 1, "Temporary failures must not be skipped as permanent")
        self.assertTrue(self.has_subtitles(vid))

    def test_permanent_failure_overwrites_stale_temporary_record(self):
        vid = "mixed00001"
        os.makedirs(os.path.join(self.tmpdir, "discarded"), exist_ok=True)
        with open(self.discarded_path(vid), "w", encoding="utf8") as f:
            f.write(f"{self.module.ERROR_TEMPORARY}: {self.module.ERROR_PROXY}: old failure\n")

        FakeYouTubeTranscriptApi.script(vid, [FakeTranscriptsDisabled("subtitles disabled")])
        self.run_scrape([vid])

        with open(self.discarded_path(vid), encoding="utf8") as f:
            reason = f.read()
        self.assertFalse(reason.startswith(self.module.ERROR_TEMPORARY), "A definite unavailability must replace the temporary flag")
        self.assertTrue(reason.startswith(self.module.ERROR_WHEN_NOT_AVAILABLE))

    def test_fallback_proxy_succeeds_after_temporary_failure(self):
        vid = "fallback001"
        FakeYouTubeTranscriptApi.script(vid, [self._proxy_error(), [FakeTranscript("fr", "French")]])

        self.run_scrape([vid])

        self.assertTrue(self.has_subtitles(vid), "The second proxy should be used after a temporary failure")
        self.assertFalse(os.path.isfile(self.discarded_path(vid)), "A video with subtitles must have no discarded record")
        # The transient record written before the successful attempt must have been removed
        self.assertEqual(os.listdir(os.path.join(self.tmpdir, "discarded")), [])

    def test_wrong_language_is_discarded_permanently(self):
        vid = "wronglang01"
        FakeYouTubeTranscriptApi.script(vid, [[FakeTranscript("en", "English")]])

        self.run_scrape([vid])

        calls = [c for c in FakeYouTubeTranscriptApi.CALLS if c[0] == vid]
        self.assertEqual(len(calls), 1, "A language mismatch must not be retried with other proxies")
        self.assertEqual(len(self.discarded_writes), 1)
        self.assertFalse(self.discarded_writes[0][2], "A language mismatch is a definite unavailability")
        self.assertFalse(self.has_subtitles(vid))

    def test_transcript_fetch_network_error_is_temporary(self):
        vid = "fetchfail01"
        import requests

        error = requests.exceptions.HTTPError("too many requests")
        # The transcripts are listed, but fetching them fails (rate limit / bad proxy)
        FakeYouTubeTranscriptApi.script(vid, [[FakeTranscript("fr", "French", fetch_error=error)]])

        self.run_scrape([vid])

        self.assertFalse(self.has_subtitles(vid))
        self.assertEqual(len(self.discarded_writes), 1)
        self.assertTrue(self.discarded_writes[0][2], "A transcript download failure is a temporary failure")

        self.module.ALL_IDS.clear()
        FakeYouTubeTranscriptApi.CALLS = []
        FakeYouTubeTranscriptApi.script(vid, [[FakeTranscript("fr", "French")]])
        self.run_scrape([vid])
        self.assertTrue(self.has_subtitles(vid))
        self.assertFalse(os.path.isfile(self.discarded_path(vid)))

    def test_unusable_proxies_are_recorded_as_temporary(self):
        vid = "noproxy0001"
        self.module.check_proxy = lambda proxy, **kwargs: proxy is None  # only the direct connection works
        FakeYouTubeTranscriptApi.script(vid, [self._proxy_error()])

        self.run_scrape([vid])

        self.assertEqual(len(self.discarded_writes), 1)
        self.assertTrue(self.discarded_writes[0][2], "Rejected proxies are a temporary problem")
        self.assertTrue(os.path.isfile(self.discarded_path(vid)))

        self.module.ALL_IDS.clear()
        FakeYouTubeTranscriptApi.CALLS = []
        self.run_scrape([vid])
        self.assertGreaterEqual(len([c for c in FakeYouTubeTranscriptApi.CALLS if c[0] == vid]), 1, "Temporary failures must be retried")

    def test_permanent_failure_after_temporary_one_stops_further_proxies(self):
        vid = "mixed00002"
        FakeYouTubeTranscriptApi.script(vid, [self._proxy_error(), FakeTranscriptsDisabled("subtitles disabled"), [FakeTranscript("fr", "French")]])

        self.run_scrape([vid], proxies=("p1", "p2", "p3"))

        calls = [c for c in FakeYouTubeTranscriptApi.CALLS if c[0] == vid]
        self.assertEqual(len(calls), 2, "A definite unavailability must stop the proxy loop")
        self.assertFalse(self.has_subtitles(vid))
        self.assertTrue(os.path.isfile(self.discarded_path(vid)))
        with open(self.discarded_path(vid), encoding="utf8") as f:
            reason = f.read()
        self.assertTrue(reason.startswith(self.module.ERROR_WHEN_NOT_AVAILABLE), "The final record must describe the definite outcome")
        self.assertEqual(os.listdir(os.path.join(self.tmpdir, "discarded")), [f"{vid}.txt"], "Only one record per video")

    def test_existing_subtitles_survive_a_later_transient_failure(self):
        vid = "resume00001"
        os.makedirs(os.path.join(self.tmpdir, "fr"), exist_ok=True)
        with open(os.path.join(self.tmpdir, "fr", f"{vid}.csv"), "w", encoding="utf8") as f:
            f.write("text;start;duration\nbonjour;0.0;1.5\n")

        FakeYouTubeTranscriptApi.script(vid, [self._proxy_error()])
        # skip_if_exists=False forces a new attempt even though the subtitles are already there
        self.run_scrape([vid], skip_if_exists=False)

        self.assertFalse(os.path.isfile(self.discarded_path(vid)), "A video with subtitles must not be recorded as discarded")

    def test_existing_audio_is_not_downloaded_again(self):
        vid = "withaudio01"
        os.makedirs(os.path.join(self.tmpdir, "mp4"), exist_ok=True)
        audio_path = os.path.join(self.tmpdir, "mp4", f"{vid}.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"already there")

        # module.YouTube raises AssertionError if any download is attempted
        self.module.extract_audio_yt(vid, self.tmpdir, skip_if_exists=True, verbose=False)
        self.assertTrue(os.path.isfile(audio_path))

    def _proxy_error(self):
        import requests

        return requests.exceptions.ProxyError("cannot connect to proxy")


if __name__ == "__main__":
    unittest.main()
