"""Local tests for the log ingestion pipeline Lambdas.

Covers three scenarios for both functions without any real AWS credentials:
  1. all records succeed
  2. a batch with some malformed records (partial failure)
  3. delivery/transform exceptions (S3 read error, Kinesis put_record error)

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import base64
import importlib.util
import io
import json
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LAMBDAS_DIR = REPO_ROOT / "Lambdas"


def load_lambda_module(module_name, filename):
    """Load a Lambda file (hyphenated name, not importable) as a module."""
    spec = importlib.util.spec_from_file_location(module_name, LAMBDAS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


producer = load_lambda_module("datastream_producer", "datastream-producer.py")
transform = load_lambda_module("delivary_stream_transform", "delivary-stream-transform.py")


# ---------------------------------------------------------------------------
# Fakes for the producer (no real boto3 / AWS calls)
# ---------------------------------------------------------------------------

class FakeBody:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload


class FakeS3Client:
    """Serves get_object from an in-memory dict; configured keys raise."""

    def __init__(self, objects, failing_keys=()):
        self.objects = objects
        self.failing_keys = set(failing_keys)

    def get_object(self, Bucket, Key):
        if Key in self.failing_keys:
            raise RuntimeError("NoSuchKey: The specified key does not exist: " + Key)
        return {"Body": FakeBody(self.objects[Key].encode("utf-8"))}


class FakeKinesisClient:
    """Records put_record calls; configured partition keys raise."""

    def __init__(self, fail_on_data_containing=()):
        self.calls = []
        self.fail_on_data_containing = list(fail_on_data_containing)

    def put_record(self, StreamName, Data, PartitionKey):
        self.calls.append({"StreamName": StreamName, "Data": Data, "PartitionKey": PartitionKey})
        for marker in self.fail_on_data_containing:
            if marker in Data:
                raise RuntimeError("ProvisionedThroughputExceededException")
        return {"ShardId": "shardId-000000000000", "SequenceNumber": "1234567890"}


def install_fake_boto3(s3_client, kinesis_client):
    """Replace the boto3 reference inside the producer module with a fake."""
    def fake_client(service, region_name=None):
        if service == "s3":
            return s3_client
        if service == "kinesis":
            return kinesis_client
        raise AssertionError("unexpected service: " + service)

    producer.boto3 = types.SimpleNamespace(client=fake_client)


def make_s3_event(entries):
    """Build an S3 event notification. Each entry is (bucket, key) or a raw dict."""
    records = []
    for entry in entries:
        if isinstance(entry, dict):
            records.append(entry)
        else:
            bucket, key = entry
            records.append({"s3": {"bucket": {"name": bucket}, "object": {"key": key}}})
    return {"Records": records}


# ---------------------------------------------------------------------------
# Helpers for the transform
# ---------------------------------------------------------------------------

def make_log_message(**overrides):
    message = {
        "timestamp": 1576280412771,
        "formatVersion": 1,
        "action": "ALLOW",
        "httpSourceName": "ALB",
        "httpSourceId": "app/my-alb/1234",
        "httpStatus": "200",
        "httpRequest": {
            "clientIp": "1.1.1.1",
            "country": "US",
            "headers": [],
            "uri": "/index.html",
            "args": "",
            "httpVersion": "HTTP/1.1",
            "httpMethod": "GET",
            "requestId": "null",
        },
    }
    message.update(overrides)
    return message


def make_firehose_record(record_id, message):
    """Encode a log message the way producer -> Kinesis -> Firehose delivers it."""
    payload = json.dumps(json.dumps(message)).encode("utf-8")  # producer double-encodes
    return {"recordId": record_id, "data": base64.b64encode(payload).decode("utf-8")}


def make_raw_firehose_record(record_id, raw_bytes):
    return {"recordId": record_id, "data": base64.b64encode(raw_bytes).decode("utf-8")}


# ---------------------------------------------------------------------------
# Producer tests
# ---------------------------------------------------------------------------

class TestDatastreamProducer(unittest.TestCase):

    def test_all_records_succeed(self):
        s3 = FakeS3Client({"logs/a.json": '{"action": "ALLOW"}',
                           "logs/b.json": '{"action": "BLOCK"}'})
        kinesis = FakeKinesisClient()
        install_fake_boto3(s3, kinesis)

        event = make_s3_event([("my-bucket", "logs/a.json"), ("my-bucket", "logs/b.json")])
        result = producer.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)
        summary = json.loads(result["body"])
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["succeeded"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["failures"], [])

        # StreamName / PartitionKey contract unchanged
        self.assertEqual(len(kinesis.calls), 2)
        for call in kinesis.calls:
            self.assertEqual(call["StreamName"], "log-stream")
            self.assertEqual(call["PartitionKey"], "aa-bb")

    def test_partial_bad_records(self):
        s3 = FakeS3Client({"logs/good.json": '{"action": "ALLOW"}'},
                          failing_keys={"logs/missing.json"})
        kinesis = FakeKinesisClient()
        install_fake_boto3(s3, kinesis)

        event = make_s3_event([
            ("my-bucket", "logs/good.json"),      # ok
            ("my-bucket", "logs/missing.json"),   # S3 read fails
            {"unexpected": "shape"},              # malformed event record
        ])
        result = producer.lambda_handler(event, None)

        summary = json.loads(result["body"])
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(summary["failed"], 2)

        # every failure names the record and the reason
        failed_ids = [f["record"] for f in summary["failures"]]
        self.assertIn("s3://my-bucket/logs/missing.json", failed_ids)
        self.assertIn("Records[2]", failed_ids)
        for failure in summary["failures"]:
            self.assertTrue(failure["reason"])

        # the good record was still delivered
        self.assertEqual(len(kinesis.calls), 1)

    def test_kinesis_put_record_failure_isolated(self):
        s3 = FakeS3Client({"logs/ok.json": '{"action": "ALLOW"}',
                           "logs/throttled.json": '{"action": "BLOCK"}'})
        kinesis = FakeKinesisClient(fail_on_data_containing=["BLOCK"])
        install_fake_boto3(s3, kinesis)

        event = make_s3_event([("my-bucket", "logs/ok.json"),
                               ("my-bucket", "logs/throttled.json")])
        result = producer.lambda_handler(event, None)

        summary = json.loads(result["body"])
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["failures"][0]["record"],
                         "s3://my-bucket/logs/throttled.json")
        self.assertIn("ProvisionedThroughputExceededException",
                      summary["failures"][0]["reason"])
        # both records were attempted; only the failing one is reported
        self.assertEqual(len(kinesis.calls), 2)


# ---------------------------------------------------------------------------
# Transform tests
# ---------------------------------------------------------------------------

class TestDeliveryStreamTransform(unittest.TestCase):

    def test_all_records_succeed(self):
        event = {"records": [
            make_firehose_record("rec-1", make_log_message()),
            make_firehose_record("rec-2", make_log_message(
                action="BLOCK", httpSourceName="-", httpStatus="404",
                httpRequest={**make_log_message()["httpRequest"],
                             "country": "XX", "httpMethod": "DELETE"})),
        ]}
        result = transform.lambda_handler(event, None)

        self.assertEqual(result["summary"]["succeeded"], 2)
        self.assertEqual(result["summary"]["failed"], 0)
        self.assertEqual(len(result["records"]), 2)

        rec1, rec2 = result["records"]
        self.assertEqual(rec1["recordId"], "rec-1")
        self.assertEqual(rec1["result"], "Ok")
        msg1 = json.loads(base64.b64decode(rec1["data"]))
        self.assertEqual(msg1["K_action"], 1)
        self.assertEqual(msg1["B_action"], 0)
        self.assertEqual(msg1["K_source"], 1)
        self.assertEqual(msg1["G_httpstatus"], 1)
        self.assertEqual(msg1["K_country"], 1)
        self.assertEqual(msg1["common_method"], 1)

        # anomaly flags on the second record
        msg2 = json.loads(base64.b64decode(rec2["data"]))
        self.assertEqual(msg2["B_action"], 1)
        self.assertEqual(msg2["UNK_source"], 1)
        self.assertEqual(msg2["B_httpstatus"], 1)
        self.assertEqual(msg2["UNK_country"], 1)
        self.assertEqual(msg2["uncommon_method"], 1)

    def test_partial_bad_records(self):
        good = make_firehose_record("rec-good", make_log_message())
        missing_field = make_log_message()
        del missing_field["httpStatus"]
        rec_missing = make_firehose_record("rec-missing-field", missing_field)
        wrong_type = make_firehose_record("rec-wrong-type",
                                          make_log_message(httpStatus=200))
        bad_base64 = {"recordId": "rec-bad-base64", "data": "!!!not-base64!!!"}
        bad_json = make_raw_firehose_record("rec-bad-json", b"{not json")

        event = {"records": [good, rec_missing, wrong_type, bad_base64, bad_json]}
        result = transform.lambda_handler(event, None)

        summary = result["summary"]
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(summary["failed"], 4)

        by_id = {r["recordId"]: r for r in result["records"]}
        self.assertEqual(by_id["rec-good"]["result"], "Ok")
        for bad_id in ["rec-missing-field", "rec-wrong-type",
                       "rec-bad-base64", "rec-bad-json"]:
            self.assertEqual(by_id[bad_id]["result"], "ProcessingFailed")

        # every failure carries the record id and a human-readable reason
        reasons = {f["recordId"]: f["reason"] for f in summary["failures"]}
        self.assertIn("httpStatus", reasons["rec-missing-field"])
        self.assertIn("httpStatus", reasons["rec-wrong-type"])
        self.assertIn("base64", reasons["rec-bad-base64"])
        self.assertIn("JSON", reasons["rec-bad-json"])

    def test_failed_record_keeps_original_payload(self):
        original = make_raw_firehose_record("rec-keep", b"not json at all")
        result = transform.lambda_handler({"records": [original]}, None)

        record = result["records"][0]
        self.assertEqual(record["result"], "ProcessingFailed")
        # payload passed through unchanged so Firehose can route it to the error prefix
        self.assertEqual(record["data"], original["data"])
        self.assertEqual(result["summary"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()
