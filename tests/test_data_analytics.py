"""Acceptance tests for the hardened ``DataAnalyzer.power_mean`` callback.

The MQTT broker is simulated with an in-memory fake client that records every
publish call, so a test can assert whether a ``MeanResult`` message was emitted
or skipped. The real ``paho.mqtt`` package is stubbed out when not installed.

Run (from the repository root):

    python -I tests/test_data_analytics.py
"""

import importlib.util
import io
import json
import logging
import sys
import types
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parent.parent
PROGRAM_DIRS = [
    REPO_ROOT / "src/solution/HandsOn_1/my_edge_app/data-analytics/program",
    REPO_ROOT / "src/solution/HandsOn_2/my_edge_app/data-analytics/program",
]

# --- Stub paho.mqtt.client: the tests never contact a real broker. This also
# keeps the suite independent of the installed paho-mqtt major version (the
# production code uses the 1.x Client() constructor).
fake_paho = types.ModuleType("paho")
fake_mqtt = types.ModuleType("paho.mqtt")
fake_client = types.ModuleType("paho.mqtt.client")


class FakeMqttClient:
    def __init__(self, *args, **kwargs):
        pass


fake_client.Client = FakeMqttClient
fake_paho.mqtt = fake_mqtt
fake_mqtt.client = fake_client
sys.modules["paho"] = fake_paho
sys.modules["paho.mqtt"] = fake_mqtt
sys.modules["paho.mqtt.client"] = fake_client


class FakeMqttBrokerClient:
    """Records publish() calls instead of contacting a broker."""

    def __init__(self):
        self.published = []
        self.subscriptions = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))

    def subscribe(self, topic):
        self.subscriptions.append(topic)


class LogCapture:
    def __init__(self):
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.logger = logging.getLogger("power_mean_acceptance_test")
        self.logger.handlers = [self.handler]
        self.logger.setLevel(logging.DEBUG)

    @property
    def text(self):
        return self.stream.getvalue()


def load_analyzer_modules():
    # Load each copy under a unique module name so both HandsOn variants are
    # actually exercised instead of being served from sys.modules cache.
    modules = []
    for index, program_dir in enumerate(PROGRAM_DIRS):
        spec = importlib.util.spec_from_file_location(
            "data_analytics_under_test_%d" % index,
            program_dir / "data_analytics.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append((program_dir, module))
    return modules


ANALYZER_MODULES = load_analyzer_modules()


def make_analyzer(module):
    analyzer = module.DataAnalyzer("power_mean_acceptance_test")
    analyzer.client = FakeMqttBrokerClient()
    return analyzer


def rows(values):
    """Simulated InfluxDB rows as they arrive through Node-RED."""
    return [{"_time": "2026-01-01T00:00:%02dZ" % i, "_value": v} for i, v in enumerate(values)]


def batch(currents, voltages, sample_number=None):
    if sample_number is None:
        sample_number = min(len(currents), len(voltages))
    return {
        "sample_number": sample_number,
        "current_drive3_batch": rows(currents),
        "voltage_drive3_batch": rows(voltages),
    }


class PowerMeanAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.capture = LogCapture()

    def _analyzer(self, module):
        analyzer = make_analyzer(module)
        # redirect the module logger to our capturing handler
        analyzer.logger = self.capture.logger
        return analyzer

    # ------------------------------------------------------------------ valid
    def test_valid_batch_publishes_correct_power_mean(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                # power per pair: 2*10=20, 3*10=30, 4*10=40 -> mean 30.0
                analyzer.power_mean(batch([2, 3, 4], [10, 10, 10]))

                self.assertEqual(len(analyzer.client.published), 1)
                topic, payload = analyzer.client.published[0]
                self.assertEqual(topic, "MeanResult")
                result = json.loads(payload)
                self.assertEqual(result["power_mean_result"], 30.0)
                self.assertEqual(result["name"], "powerdrive3_mean")
                self.assertIn("power mean result: 30.0", self.capture.text)

    def test_power_mean_with_rounded_result(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                # (1*1 + 2*2 + 3*3) / 3 = 14/3 = 4.67
                analyzer.power_mean(batch([1, 2, 3], [1, 2, 3]))
                topic, payload = analyzer.client.published[0]
                self.assertEqual(topic, "MeanResult")
                self.assertEqual(json.loads(payload)["power_mean_result"], 4.67)

    # --------------------------------------------------------------- invalid
    def test_empty_pairing_publishes_nothing_and_logs_reason(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                analyzer.power_mean(batch([], []))
                self.assertEqual(analyzer.client.published, [])
                self.assertIn("empty pairing", self.capture.text)

    def test_one_side_empty_publishes_nothing(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                analyzer.power_mean(batch([], [10, 20]))
                self.assertEqual(analyzer.client.published, [])
                self.assertIn("empty pairing", self.capture.text)

    def test_length_mismatch_publishes_nothing_and_logs_reason(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                analyzer.power_mean(batch([1, 2, 3], [10, 20], sample_number=3))
                self.assertEqual(analyzer.client.published, [])
                self.assertIn("batch lengths differ", self.capture.text)

    def test_missing_field_publishes_nothing_and_logs_reason(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                analyzer.power_mean({
                    "current_drive3_batch": rows([1]),
                    "voltage_drive3_batch": rows([10]),
                })
                self.assertEqual(analyzer.client.published, [])
                self.assertIn("missing field(s)", self.capture.text)
                self.assertIn("sample_number", self.capture.text)

    def test_sample_number_mismatch_publishes_nothing(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                analyzer.power_mean(batch([1, 2], [10, 20], sample_number=5))
                self.assertEqual(analyzer.client.published, [])
                self.assertIn("does not match", self.capture.text)

    def test_non_dict_payload_publishes_nothing(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                for bad in (None, [], "nope", 42):
                    analyzer.client.published.clear()
                    analyzer.power_mean(bad)
                    self.assertEqual(analyzer.client.published, [])
                    self.assertIn("not an object", self.capture.text)

    def test_non_list_batches_publish_nothing(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                analyzer.power_mean({
                    "sample_number": 1,
                    "current_drive3_batch": "not-a-list",
                    "voltage_drive3_batch": rows([10]),
                })
                self.assertEqual(analyzer.client.published, [])
                self.assertIn("must be lists", self.capture.text)

    def test_row_without_value_field_publishes_nothing(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                payload = {
                    "sample_number": 1,
                    "current_drive3_batch": [{"_time": "t0"}],  # no _value
                    "voltage_drive3_batch": rows([10]),
                }
                analyzer.power_mean(payload)
                self.assertEqual(analyzer.client.published, [])
                self.assertIn("_value", self.capture.text)

    def test_invalid_sample_number_publishes_nothing(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                for bad in (0, -1, "3", 2.0, True):
                    analyzer.client.published.clear()
                    analyzer.power_mean(batch([1], [10], sample_number=bad))
                    self.assertEqual(analyzer.client.published, [], "bad sample_number %r" % (bad,))

    # ----------------------------------------------------- on_message hardening
    def test_malformed_json_does_not_crash_and_publishes_nothing(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                message = types.SimpleNamespace(topic="Mean", payload=b"{not valid json")
                analyzer.on_message(analyzer.client, None, message)
                self.assertEqual(analyzer.client.published, [])
                self.assertIn("not valid JSON", self.capture.text)

    def test_on_message_routes_valid_mean_message(self):
        for _, module in ANALYZER_MODULES:
            with self.subTest(module=module.__name__):
                analyzer = self._analyzer(module)
                # mimic handle_data(): the 'Mean' topic routes to power_mean
                analyzer.subscribe("Mean", analyzer.power_mean)
                payload = batch([2, 4], [5, 5])
                message = types.SimpleNamespace(topic="Mean", payload=json.dumps(payload).encode())
                analyzer.on_message(analyzer.client, None, message)
                self.assertEqual(len(analyzer.client.published), 1)
                self.assertEqual(analyzer.client.published[0][0], "MeanResult")


if __name__ == "__main__":
    unittest.main(verbosity=2)
