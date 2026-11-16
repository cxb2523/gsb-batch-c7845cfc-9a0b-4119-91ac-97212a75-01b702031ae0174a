"""unittest module, fault tolerant coroutine execution"""

# pylint: disable=wrong-import-position,unused-argument
import sys
from contextlib import contextmanager
from unittest import TestCase, mock, main

import gevent

sys.path.insert(0, "src")
from GeventLibrary.keywords.gevent_keywords import (  # noqa: E402
    CoroutineResult,
    CoroutineStatus,
    GeventKeywords,
)


@contextmanager
def patched_run_keyword(side_effect):
    """patch the robot built-in keyword execution and the robot execution context"""
    with mock.patch(
        "robot.libraries.BuiltIn.BuiltIn.run_keyword", side_effect=side_effect
    ), mock.patch(
        "robot.running.context.ExecutionContexts.current",
        return_value="not null...",
    ):
        yield


def failing_keyword(value="ok", message="some value error..."):
    """returns a `run_keyword` side effect raising for the failing keyword"""
    written = []

    def run_keyword(keyword_name, *args, **kwargs):
        if keyword_name == "Fail":
            raise ValueError(message)
        if keyword_name == "Slow":
            gevent.sleep(10)
            written.append(keyword_name)
            return "slow result"
        written.append(keyword_name)
        return value

    run_keyword.written = written
    return run_keyword


class TestRunCoroutinesTolerantMode(TestCase):
    """run_coroutines with raise_on_failure=False settles every coroutine"""

    def test_mixed_success_and_failure_returns_results_in_order(self):
        """a single failing coroutine must not discard the other results"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Succeed", "first", alias="my_alias")
        gevent_library_instance.add_coroutine("Fail", alias="my_alias")
        gevent_library_instance.add_coroutine("Succeed", "third", alias="my_alias")

        with patched_run_keyword(failing_keyword("succeeded")):
            results = gevent_library_instance.run_coroutines(
                alias="my_alias", raise_on_failure=False
            )

        self.assertEqual(3, len(results))
        self.assertTrue(all(isinstance(result, CoroutineResult) for result in results))

        # order of `Add Coroutine` is preserved
        self.assertEqual(
            ["Succeed", "Fail", "Succeed"],
            [result["keyword_name"] for result in results],
        )
        self.assertEqual(
            ["completed", "exception", "completed"],
            [result["status"] for result in results],
        )
        self.assertEqual("succeeded", results[0]["value"])
        self.assertEqual("succeeded", results[2]["value"])

        failed = results[1]
        self.assertIsNone(failed["value"])
        self.assertEqual("ValueError", failed["exception_type"])
        self.assertEqual("some value error...", failed["exception_message"])
        self.assertIsInstance(failed["exception"], ValueError)
        self.assertIn("ValueError", failed["reason"])
        self.assertIn("some value error...", failed["reason"])
        self.assertTrue(failed["failed"])
        self.assertFalse(failed["successful"])
        self.assertFalse(failed["timed_out"])

    def test_all_failures_return_results_without_raising(self):
        """when every coroutine fails, the results are returned instead of raising"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Fail", alias="my_alias")
        gevent_library_instance.add_coroutine("Fail", alias="my_alias")

        with patched_run_keyword(failing_keyword()):
            results = gevent_library_instance.run_coroutines(
                alias="my_alias", raise_on_failure=False
            )

        self.assertEqual(2, len(results))
        self.assertEqual(["exception", "exception"], [r["status"] for r in results])
        self.assertEqual(
            ["ValueError", "ValueError"], [r["exception_type"] for r in results]
        )

    def test_timeout_returns_completed_items_and_kills_running_greenlet(self):
        """coroutines that exceed the timeout are reported and terminated"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Succeed", "fast", alias="my_alias")
        gevent_library_instance.add_coroutine("Slow", alias="my_alias")

        side_effect = failing_keyword("fast result")
        with patched_run_keyword(side_effect):
            results = gevent_library_instance.run_coroutines(
                alias="my_alias", timeout=0.1, raise_on_failure=False
            )
            # the timed out greenlet must not keep running in the background
            gevent.sleep(0.3)
            self.assertEqual(["Succeed"], side_effect.written)

        self.assertEqual(2, len(results))
        self.assertEqual("completed", results[0]["status"])
        self.assertEqual("fast result", results[0]["value"])
        self.assertEqual("timeout", results[1]["status"])
        self.assertIsNone(results[1]["value"])
        self.assertTrue(results[1]["timed_out"])
        self.assertFalse(results[1]["successful"])
        self.assertIsNotNone(results[1]["reason"])

    def test_bundle_is_empty_after_tolerant_execution_and_reusable(self):
        """the bundle survives the execution as an empty, reusable bundle"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Succeed", "first", alias="my_alias")
        gevent_library_instance.add_coroutine("Fail", alias="my_alias")

        with patched_run_keyword(failing_keyword("first result")):
            gevent_library_instance.run_coroutines(
                alias="my_alias", raise_on_failure=False
            )
            coros = gevent_library_instance["my_alias"]
            self.assertEqual(0, len(coros))

            # the same alias can be used again
            gevent_library_instance.add_coroutine("Succeed", "again", alias="my_alias")
            self.assertEqual(1, len(coros))
            results = gevent_library_instance.run_coroutines(
                alias="my_alias", raise_on_failure=False
            )
            self.assertEqual(0, len(coros))

        self.assertEqual(1, len(results))
        self.assertEqual("completed", results[0]["status"])
        self.assertEqual("first result", results[0]["value"])

    def test_bundle_is_empty_after_timed_out_execution(self):
        """a timed out execution also leaves the bundle empty"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Slow", alias="my_alias")

        with patched_run_keyword(failing_keyword()):
            results = gevent_library_instance.run_coroutines(
                alias="my_alias", timeout=0.1, raise_on_failure=False
            )
            self.assertEqual(0, len(gevent_library_instance["my_alias"]))

        self.assertEqual("timeout", results[0]["status"])

    def test_timeout_with_limited_pool_reports_waiting_coroutines(self):
        """with a small pool the timeout also covers coroutines waiting for a slot"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        for _ in range(3):
            gevent_library_instance.add_coroutine("Slow", alias="my_alias")

        slow = mock.Mock(side_effect=lambda *args, **kwargs: gevent.sleep(5))
        with patched_run_keyword(slow):
            started = gevent.get_hub().loop.now()
            results = gevent_library_instance.run_coroutines(
                alias="my_alias",
                timeout=0.2,
                gevent_pool_size=1,
                raise_on_failure=False,
            )
            elapsed = gevent.get_hub().loop.now() - started

        self.assertEqual(3, len(results))
        self.assertEqual(["timeout"] * 3, [r["status"] for r in results])
        # the execution does not wait for the queued coroutines to finish
        self.assertLess(elapsed, 2)
        # only the coroutine that got the pool slot was actually started
        self.assertEqual(1, slow.call_count)

    def test_continue_on_failure_alias_enables_tolerant_mode(self):
        """`continue_on_failure` is the inverted alias of `raise_on_failure`"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Fail", alias="my_alias")

        with patched_run_keyword(failing_keyword()):
            results = gevent_library_instance.run_coroutines(
                alias="my_alias", continue_on_failure=True
            )

        self.assertEqual("exception", results[0]["status"])

    def test_string_boolean_values_are_accepted(self):
        """robot style boolean strings are honoured as well"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Fail", alias="my_alias")

        with patched_run_keyword(failing_keyword()):
            results = gevent_library_instance.run_coroutines(
                alias="my_alias", raise_on_failure="False"
            )
        self.assertEqual("exception", results[0]["status"])

    def test_unknown_failure_option_is_rejected(self):
        """typos in the failure strategy parameters must not pass silently"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Succeed", "value", alias="my_alias")

        with patched_run_keyword(failing_keyword("value")):
            with self.assertRaises(TypeError) as exp:
                gevent_library_instance.run_coroutines(
                    alias="my_alias", ignor_errors=True
                )
        self.assertIn("ignor_errors", str(exp.exception))
        # an invalid parameter does not start an execution, the bundle is kept intact
        self.assertEqual(1, len(gevent_library_instance["my_alias"]))

    def test_result_supports_item_and_attribute_access(self):
        """a result is usable both as a dict and as an object"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Fail", alias="my_alias")

        with patched_run_keyword(failing_keyword()):
            results = gevent_library_instance.run_coroutines(
                alias="my_alias", raise_on_failure=False
            )

        result = results[0]
        self.assertEqual(result["status"], result.status)
        self.assertEqual("exception", result.status)
        self.assertEqual(CoroutineStatus.EXCEPTION, result.status_enum)
        self.assertEqual("ValueError", result.error_type)
        self.assertEqual("some value error...", result.error_message)
        self.assertEqual(result["reason"], result.failure_reason)
        self.assertEqual(
            {
                "keyword_name": "Fail",
                "status": "exception",
                "value": None,
                "exception": result["exception"],
                "exception_type": "ValueError",
                "exception_message": "some value error...",
                "reason": "ValueError: some value error...",
                "successful": False,
                "failed": True,
                "timed_out": False,
            },
            result.to_dict(),
        )


class TestRunCoroutinesDefaultMode(TestCase):
    """the default behaviour must stay backward compatible"""

    def test_default_mode_raises_the_original_exception(self):
        """by default a failing coroutine raises the original exception"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Succeed", "first", alias="my_alias")
        gevent_library_instance.add_coroutine("Fail", alias="my_alias")

        with patched_run_keyword(failing_keyword()):
            with self.assertRaises(ValueError) as exp:
                gevent_library_instance.run_coroutines(alias="my_alias")
        self.assertEqual("some value error...", str(exp.exception))

    def test_default_mode_raise_on_failure_true_is_the_same(self):
        """explicitly passing raise_on_failure=True keeps the fail fast behaviour"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Fail", alias="my_alias")

        with patched_run_keyword(failing_keyword()):
            with self.assertRaises(ValueError):
                gevent_library_instance.run_coroutines(
                    alias="my_alias", raise_on_failure=True
                )

    def test_default_mode_still_returns_values_in_order(self):
        """successful executions keep returning the plain values list"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Succeed", "first", alias="my_alias")
        gevent_library_instance.add_coroutine("Succeed", "second", alias="my_alias")

        with patched_run_keyword(failing_keyword("a value")):
            values = gevent_library_instance.run_coroutines(alias="my_alias")

        self.assertEqual(["a value", "a value"], values)
        self.assertNotIsInstance(values[0], CoroutineResult)

    def test_bundle_is_empty_after_failure_in_default_mode(self):
        """the bundle is cleared even when the execution raises"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Succeed", "first", alias="my_alias")
        gevent_library_instance.add_coroutine("Fail", alias="my_alias")

        with patched_run_keyword(failing_keyword()):
            with self.assertRaises(ValueError):
                gevent_library_instance.run_coroutines(alias="my_alias")
            self.assertEqual(0, len(gevent_library_instance["my_alias"]))

    def test_default_mode_keeps_raising_when_the_failure_happens_before_the_timeout(
        self,
    ):
        """a coroutine that already failed is still reported even if others time out"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Fail", alias="my_alias")
        gevent_library_instance.add_coroutine("Slow", alias="my_alias")

        with patched_run_keyword(failing_keyword()):
            with self.assertRaises(ValueError) as exp:
                gevent_library_instance.run_coroutines(alias="my_alias", timeout=0.2)
        self.assertEqual("some value error...", str(exp.exception))
        self.assertEqual(0, len(gevent_library_instance["my_alias"]))

    def test_bundle_is_empty_after_timeout_in_default_mode(self):
        """the bundle is cleared and running coroutines are stopped on timeout"""
        gevent_library_instance = GeventKeywords()
        gevent_library_instance.create_gevent_bundle(alias="my_alias")
        gevent_library_instance.add_coroutine("Succeed", "fast", alias="my_alias")
        gevent_library_instance.add_coroutine("Slow", alias="my_alias")

        side_effect = failing_keyword("fast result")
        with patched_run_keyword(side_effect):
            values = gevent_library_instance.run_coroutines(
                alias="my_alias", timeout=0.1
            )
            gevent.sleep(0.3)
            self.assertEqual(["Succeed"], side_effect.written)
            self.assertEqual(0, len(gevent_library_instance["my_alias"]))

        # unfinished coroutines do not expose a meaningful value
        self.assertEqual("fast result", values[0])
        self.assertIsNone(values[1])


if __name__ == "__main__":
    main()
