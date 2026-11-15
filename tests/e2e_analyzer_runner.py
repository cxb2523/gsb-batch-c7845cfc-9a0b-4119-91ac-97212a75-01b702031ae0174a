"""CLI bridge used by the cross-language end-to-end test.

Reads a list of MQTT-like messages ({"topic": ..., "payload": <json string>})
from a JSON file given as argv[1], feeds them through the real
DataAnalyzer.on_message pipeline with a fake MQTT client, and prints the
published MeanResult messages as JSON:

    python -I tests/e2e_analyzer_runner.py messages.json

Output: {"published": [[topic, payload_string], ...], "log": "..."}
"""

import io
import json
import logging
import sys
import types
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parent.parent
PROGRAM_DIR = REPO_ROOT / "src/solution/HandsOn_1/my_edge_app/data-analytics/program"

# Stub paho so no broker / specific paho version is required.
fake_paho = types.ModuleType("paho")
fake_mqtt = types.ModuleType("paho.mqtt")
fake_client = types.ModuleType("paho.mqtt.client")


class _StubClient:
    def __init__(self, *args, **kwargs):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))

    def subscribe(self, topic):
        pass

    def username_pw_set(self, *args, **kwargs):
        pass

    def connect(self, *args, **kwargs):
        pass

    def loop_start(self):
        pass


fake_client.Client = _StubClient
fake_paho.mqtt = fake_mqtt
fake_mqtt.client = fake_client
sys.modules["paho"] = fake_paho
sys.modules["paho.mqtt"] = fake_mqtt
sys.modules["paho.mqtt.client"] = fake_client

sys.path.insert(0, str(PROGRAM_DIR))
import data_analytics  # noqa: E402

stream = io.StringIO()
handler = logging.StreamHandler(stream)
handler.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
logger = logging.getLogger("e2e.data_analytics")
logger.handlers = [handler]
logger.setLevel(logging.DEBUG)

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    messages = json.load(handle)

analyzer = data_analytics.DataAnalyzer("e2e")
analyzer.logger = logger
analyzer.subscribe("Mean", analyzer.power_mean)

for message in messages:
    mqtt_message = types.SimpleNamespace(
        topic=message["topic"],
        payload=message["payload"].encode("utf-8"),
    )
    analyzer.on_message(analyzer.client, None, mqtt_message)

print(json.dumps({"published": analyzer.client.published, "log": stream.getvalue()}))
