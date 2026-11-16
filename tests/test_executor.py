"""Local, self-contained tests for the OrbitPilot executor skeleton.

Run with:  python -m unittest discover -s tests -v
No browser, no network, no third-party packages required.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orbitpilot import (  # noqa: E402
    PageAdapter,
    PageError,
    StaticPageAdapter,
    load_yaml,
    render_manifest,
    run_strategy,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES_DIR = os.path.join(REPO_ROOT, "samples")


class FakeAdapter(PageAdapter):
    """In-memory adapter used to test executor logic in isolation."""

    def __init__(self, texts=None, missing=(), calls=None):
        self.texts = texts or {}
        self.missing = set(missing)
        self.calls = calls if calls is not None else []

    def click(self, selector):
        self.calls.append(("click", selector))
        if selector in self.missing:
            raise PageError("selector %r not found" % (selector,))

    def read_text(self, selector):
        self.calls.append(("read_text", selector))
        if selector in self.missing:
            raise PageError("selector %r not found" % (selector,))
        return self.texts.get(selector, "")

    def navigate(self, url):
        self.calls.append(("navigate", url))
        if url in self.missing:
            raise PageError("page not found: %r" % (url,))


def statuses(manifest):
    return [step["status"] for step in manifest["steps"]]


def reasons(manifest):
    return [step["reason"] for step in manifest["steps"]]


class HappyPathTests(unittest.TestCase):
    def test_all_steps_succeed_and_run_is_complete(self):
        config = {
            "strategy": [
                {"name": "open", "action": "navigate", "url": "home.html"},
                {"name": "enter", "action": "click", "selector": "#bet"},
                {
                    "name": "observe",
                    "action": "read_text",
                    "selector": "#mult",
                    "store_as": "multiplier",
                },
                {
                    "name": "exit",
                    "action": "navigate",
                    "url": "exit.html",
                    "when": "multiplier == '2.45'",
                },
            ]
        }
        adapter = FakeAdapter(texts={"#mult": "2.45"})
        manifest = run_strategy(config, adapter)

        self.assertTrue(manifest["complete"])
        self.assertEqual(statuses(manifest), ["success"] * 4)
        self.assertEqual(manifest["steps"][2]["value"], "2.45")
        # The `when` gate saw the stored variable, so the navigate ran.
        self.assertIn(("navigate", "exit.html"), adapter.calls)

    def test_manifest_records_name_action_status_reason(self):
        config = {"strategy": [{"name": "a", "action": "click", "selector": "#x"}]}
        manifest = run_strategy(config, FakeAdapter())
        step = manifest["steps"][0]
        self.assertEqual(step["name"], "a")
        self.assertEqual(step["action"], "click")
        self.assertEqual(step["status"], "success")
        self.assertEqual(step["reason"], "")


class ConditionSkipTests(unittest.TestCase):
    def test_condition_mismatch_is_skipped_not_failed(self):
        config = {
            "strategy": [
                {
                    "name": "enter",
                    "action": "click",
                    "selector": "#bet",
                    "condition": {"type": "text_match", "value": "Available"},
                },
                {"name": "after", "action": "click", "selector": "#next"},
            ]
        }
        adapter = FakeAdapter(texts={"#bet": "Busy"})
        manifest = run_strategy(config, adapter)

        self.assertEqual(statuses(manifest), ["skipped", "success"])
        self.assertIn("Available", manifest["steps"][0]["reason"])
        self.assertTrue(manifest["complete"])  # skips do not break completeness
        # The skipped step's action never ran; the later step did.
        self.assertEqual(adapter.calls, [("read_text", "#bet"), ("click", "#next")])

    def test_condition_may_target_a_different_selector(self):
        config = {
            "strategy": [
                {
                    "name": "enter",
                    "action": "click",
                    "selector": "#bet",
                    "condition": {
                        "type": "text_match",
                        "selector": "#status",
                        "value": "Open",
                    },
                }
            ]
        }
        adapter = FakeAdapter(texts={"#status": "Open"})
        manifest = run_strategy(config, adapter)
        self.assertEqual(statuses(manifest), ["success"])
        self.assertIn(("click", "#bet"), adapter.calls)


class WhenGateTests(unittest.TestCase):
    def test_when_false_skips_without_executing(self):
        config = {
            "strategy": [
                {
                    "name": "observe",
                    "action": "read_text",
                    "selector": "#mult",
                    "store_as": "multiplier",
                },
                {
                    "name": "exit",
                    "action": "navigate",
                    "url": "exit.html",
                    "when": "multiplier == '9.99'",
                },
            ]
        }
        adapter = FakeAdapter(texts={"#mult": "2.45"})
        manifest = run_strategy(config, adapter)

        self.assertEqual(statuses(manifest), ["success", "skipped"])
        self.assertNotIn(("navigate", "exit.html"), adapter.calls)
        self.assertTrue(manifest["complete"])

    def test_when_with_undefined_variable_skips_with_reason(self):
        config = {
            "strategy": [
                {
                    "name": "exit",
                    "action": "navigate",
                    "url": "exit.html",
                    "when": "never_stored > 2.0",
                },
                {"name": "after", "action": "click", "selector": "#ok"},
            ]
        }
        adapter = FakeAdapter()
        manifest = run_strategy(config, adapter)  # must not raise

        self.assertEqual(statuses(manifest), ["skipped", "success"])
        self.assertIn("never_stored", manifest["steps"][0]["reason"])
        self.assertNotIn(("navigate", "exit.html"), adapter.calls)

    def test_when_supports_numeric_comparison_on_stored_text(self):
        config = {
            "strategy": [
                {
                    "name": "observe",
                    "action": "read_text",
                    "selector": "#mult",
                    "store_as": "multiplier",
                },
                {
                    "name": "exit",
                    "action": "navigate",
                    "url": "exit.html",
                    "when": "multiplier > '2.0'",
                },
            ]
        }
        adapter = FakeAdapter(texts={"#mult": "2.45"})
        manifest = run_strategy(config, adapter)
        self.assertEqual(statuses(manifest), ["success", "success"])
        self.assertIn(("navigate", "exit.html"), adapter.calls)


class FailureIsolationTests(unittest.TestCase):
    def test_single_failure_does_not_abort_and_marks_run_incomplete(self):
        config = {
            "strategy": [
                {"name": "before", "action": "click", "selector": "#ok"},
                {"name": "boom", "action": "click", "selector": "#missing"},
                {"name": "after", "action": "click", "selector": "#ok2"},
            ]
        }
        adapter = FakeAdapter(missing={"#missing"})
        manifest = run_strategy(config, adapter)

        self.assertEqual(statuses(manifest), ["success", "failed", "success"])
        self.assertFalse(manifest["complete"])
        # All executed results are preserved, including the failure reason.
        self.assertEqual(len(manifest["steps"]), 3)
        self.assertIn("#missing", reasons(manifest)[1])
        # The step after the failure still ran.
        self.assertEqual(
            adapter.calls,
            [("click", "#ok"), ("click", "#missing"), ("click", "#ok2")],
        )

    def test_unknown_action_fails_but_run_continues(self):
        config = {
            "strategy": [
                {"name": "bad", "action": "teleport"},
                {"name": "after", "action": "click", "selector": "#ok"},
            ]
        }
        manifest = run_strategy(config, FakeAdapter())
        self.assertEqual(statuses(manifest), ["failed", "success"])
        self.assertFalse(manifest["complete"])


class DeterminismTests(unittest.TestCase):
    def test_same_config_yields_identical_manifest(self):
        config = {
            "strategy": [
                {"name": "a", "action": "click", "selector": "#ok"},
                {
                    "name": "b",
                    "action": "click",
                    "selector": "#bet",
                    "condition": {"type": "text_match", "value": "Nope"},
                },
                {"name": "c", "action": "click", "selector": "#missing"},
                {
                    "name": "d",
                    "action": "navigate",
                    "url": "x.html",
                    "when": "ghost == 1",
                },
            ]
        }
        first = run_strategy(config, FakeAdapter(missing={"#missing"}))
        second = run_strategy(config, FakeAdapter(missing={"#missing"}))
        self.assertEqual(first, second)
        self.assertEqual(render_manifest(first), render_manifest(second))
        self.assertIn("INCOMPLETE", render_manifest(first))


class StaticAdapterSampleTests(unittest.TestCase):
    """End-to-end run of the shipped sample config on local static pages."""

    def test_sample_config_runs_clean(self):
        with open(os.path.join(SAMPLES_DIR, "orbit.yaml"), encoding="utf-8") as fh:
            config = load_yaml(fh.read())
        adapter = StaticPageAdapter(
            os.path.join(SAMPLES_DIR, "pages"),
            start_page=config["session"]["start_page"],
        )
        manifest = run_strategy(config, adapter)

        self.assertTrue(manifest["complete"], render_manifest(manifest))
        self.assertEqual(
            statuses(manifest), ["success"] * len(config["strategy"])
        )
        # click followed data-href to the dashboard, so its text was readable.
        self.assertEqual(manifest["steps"][1]["value"], "Balance: 128.50")

    def test_static_adapter_rejects_remote_urls(self):
        adapter = StaticPageAdapter(os.path.join(SAMPLES_DIR, "pages"))
        config = {
            "strategy": [
                {"name": "web", "action": "navigate", "url": "https://example.com/"}
            ]
        }
        manifest = run_strategy(config, adapter)
        self.assertEqual(statuses(manifest), ["failed"])
        self.assertFalse(manifest["complete"])


class YamlMiniTests(unittest.TestCase):
    def test_parses_nested_strategy_config(self):
        text = """
        session:
          browser: "chrome"
          headless: false
        strategy:
          - name: "enter_bet"
            action: "click"
            selector: "#bet_button"
            condition:
              type: "text_match"
              value: "Available"
          - name: "conditional_exit"
            action: "navigate"
            url: "https://example.com/exit"
            when: "current_multiplier > 2.0"
        """
        config = load_yaml(text)
        self.assertEqual(config["session"]["browser"], "chrome")
        self.assertIs(config["session"]["headless"], False)
        first, second = config["strategy"]
        self.assertEqual(first["condition"]["value"], "Available")
        self.assertEqual(second["url"], "https://example.com/exit")
        self.assertEqual(second["when"], "current_multiplier > 2.0")


if __name__ == "__main__":
    unittest.main()
