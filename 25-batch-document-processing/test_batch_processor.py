# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Offline tests for the per-file batch settlement logic in batch_processor.py.

These tests use a fake GCS client and a fake Document AI client — no real
GCP credentials, project, or network access are required.

Run with either:
  python -m unittest test_batch_processor -v
  python -m pytest test_batch_processor.py -v

Scenarios covered:
  1. All output files succeed          -> all records settled, zero failures
  2. A single corrupt/unreadable file  -> only that file fails, rest settle
  3. Batch submission/polling failure  -> reported as failure, never success
"""

import concurrent.futures
import json
import unittest
from unittest import mock

from google.api_core import exceptions as gax

import batch_processor


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeBlob:
    """Minimal stand-in for google.cloud.storage.Blob."""

    def __init__(self, name, payload: bytes | None = None, error: Exception | None = None):
        self.name = name
        self._payload = payload
        self._error = error

    def download_as_bytes(self) -> bytes:
        if self._error is not None:
            raise self._error
        return self._payload


class FakeGCSClient:
    """Fake storage.Client: serves a fixed list of blobs from list_blobs."""

    def __init__(self, blobs):
        self._blobs = list(blobs)

    def list_blobs(self, bucket_name, prefix=None):
        return [b for b in self._blobs if prefix is None or b.name.startswith(prefix)]


class FakeOperation:
    """Fake LRO handle returned by batch_process_documents."""

    def __init__(self, name="projects/p/operations/123", result_error=None):
        self.operation = mock.Mock(name="operation")
        self.operation.name = name
        self._result_error = result_error
        self.result_calls = 0

    def result(self, timeout=None):
        self.result_calls += 1
        if self._result_error is not None:
            raise self._result_error
        return None


class FakeDocAIClient:
    """Fake DocumentProcessorServiceClient."""

    def __init__(self, operation=None, submit_error=None):
        self._operation = operation
        self._submit_error = submit_error
        self.submitted_requests = []

    def processor_path(self, project, location, processor):
        return f"projects/{project}/locations/{location}/processors/{processor}"

    def batch_process_documents(self, request=None):
        if self._submit_error is not None:
            raise self._submit_error
        self.submitted_requests.append(request)
        return self._operation


def make_doc_payload(text: str, num_pages: int) -> bytes:
    return json.dumps({
        "text": text,
        "pages": [{"pageNumber": i + 1} for i in range(num_pages)],
    }).encode("utf-8")


# ---------------------------------------------------------------------------
# Scenario 1: all files succeed
# ---------------------------------------------------------------------------

class TestReadBatchOutputAllSuccess(unittest.TestCase):
    def test_all_files_settle(self):
        blobs = [
            FakeBlob("docai/output/doc1/0.json", make_doc_payload("alpha text", 2)),
            FakeBlob("docai/output/doc2/0.json", make_doc_payload("beta " * 100, 5)),
            FakeBlob("docai/output/README.txt", b"not json, not a .json blob"),
        ]
        gcs = FakeGCSClient(blobs)

        settlement = batch_processor.read_batch_output("fake-bucket", gcs_client=gcs)

        self.assertEqual(settlement.succeeded, 2)
        self.assertEqual(settlement.failed, 0)
        self.assertEqual(settlement.failures, [])

        rec1, rec2 = settlement.records
        # Stable downstream schema: blob_name, pages, text_length, preview
        self.assertEqual(set(rec1.keys()), {"blob_name", "pages", "text_length", "preview"})
        self.assertEqual(rec1["blob_name"], "docai/output/doc1/0.json")
        self.assertEqual(rec1["pages"], 2)
        self.assertEqual(rec1["text_length"], len("alpha text"))
        self.assertEqual(rec1["preview"], "alpha text")

        self.assertEqual(rec2["pages"], 5)
        self.assertEqual(rec2["text_length"], len("beta " * 100))
        # preview is capped at 200 chars
        long_text = "beta " * 100
        self.assertEqual(rec2["preview"], long_text[:200])

    def test_empty_output_prefix_settles_nothing(self):
        settlement = batch_processor.read_batch_output(
            "fake-bucket", gcs_client=FakeGCSClient([])
        )
        self.assertEqual(settlement.succeeded, 0)
        self.assertEqual(settlement.failed, 0)


# ---------------------------------------------------------------------------
# Scenario 2: a single corrupt file only affects itself
# ---------------------------------------------------------------------------

class TestReadBatchOutputPartialFailure(unittest.TestCase):
    def test_corrupt_json_only_fails_that_file(self):
        blobs = [
            FakeBlob("docai/output/good/0.json", make_doc_payload("good doc", 1)),
            FakeBlob("docai/output/bad/0.json", b"{not valid json!!!"),
            FakeBlob("docai/output/also-good/0.json", make_doc_payload("another good one", 3)),
        ]
        settlement = batch_processor.read_batch_output(
            "fake-bucket", gcs_client=FakeGCSClient(blobs)
        )

        self.assertEqual(settlement.succeeded, 2)
        self.assertEqual(settlement.failed, 1)

        # The two good files still produce full records
        names = {r["blob_name"] for r in settlement.records}
        self.assertEqual(names, {"docai/output/good/0.json", "docai/output/also-good/0.json"})

        # The failure carries blob name and a checkable reason
        failure = settlement.failures[0]
        self.assertEqual(failure["blob_name"], "docai/output/bad/0.json")
        self.assertIn("error", failure)
        self.assertTrue(failure["error"])  # non-empty reason

    def test_download_error_only_fails_that_file(self):
        blobs = [
            FakeBlob("docai/output/good/0.json", make_doc_payload("fine", 1)),
            FakeBlob("docai/output/broken/0.json",
                     error=gax.ServiceUnavailable("GCS temporarily unavailable")),
        ]
        settlement = batch_processor.read_batch_output(
            "fake-bucket", gcs_client=FakeGCSClient(blobs)
        )

        self.assertEqual(settlement.succeeded, 1)
        self.assertEqual(settlement.failed, 1)
        self.assertEqual(settlement.records[0]["blob_name"], "docai/output/good/0.json")
        failure = settlement.failures[0]
        self.assertEqual(failure["blob_name"], "docai/output/broken/0.json")
        self.assertIn("ServiceUnavailable", failure["error"])

    def test_wrong_schema_json_counts_as_failure_not_crash(self):
        # Valid JSON but payload is a JSON array, not a Document object —
        # must be settled as a per-file failure, not blow up the batch.
        blobs = [
            FakeBlob("docai/output/weird/0.json", json.dumps([1, 2, 3]).encode()),
            FakeBlob("docai/output/good/0.json", make_doc_payload("ok", 1)),
        ]
        settlement = batch_processor.read_batch_output(
            "fake-bucket", gcs_client=FakeGCSClient(blobs)
        )
        self.assertEqual(settlement.succeeded, 1)
        self.assertEqual(settlement.failed, 1)
        self.assertEqual(settlement.failures[0]["blob_name"], "docai/output/weird/0.json")


# ---------------------------------------------------------------------------
# Scenario 3: submission failure / polling timeout are never disguised
# ---------------------------------------------------------------------------

class TestRunBatchProcessFailure(unittest.TestCase):
    def _run_with_client(self, fake_client):
        with mock.patch.object(
            batch_processor.documentai,
            "DocumentProcessorServiceClient",
            return_value=fake_client,
        ):
            return batch_processor.run_batch_process("proc-123", "fake-bucket")

    def test_successful_operation_returns_true(self):
        op = FakeOperation()
        result = self._run_with_client(FakeDocAIClient(operation=op))
        self.assertTrue(result)
        self.assertEqual(op.result_calls, 1)

    def test_submit_permission_denied_returns_false(self):
        client = FakeDocAIClient(submit_error=gax.PermissionDenied("denied"))
        self.assertFalse(self._run_with_client(client))

    def test_submit_unexpected_error_returns_false(self):
        client = FakeDocAIClient(submit_error=RuntimeError("boom"))
        self.assertFalse(self._run_with_client(client))

    def test_poll_timeout_returns_false(self):
        op = FakeOperation(result_error=concurrent.futures.TimeoutError())
        self.assertFalse(self._run_with_client(FakeDocAIClient(operation=op)))

    def test_poll_operation_error_returns_false(self):
        op = FakeOperation(result_error=gax.InternalServerError("LRO failed"))
        self.assertFalse(self._run_with_client(FakeDocAIClient(operation=op)))


# ---------------------------------------------------------------------------
# Settlement container
# ---------------------------------------------------------------------------

class TestBatchSettlement(unittest.TestCase):
    def test_counts(self):
        s = batch_processor.BatchSettlement()
        self.assertEqual((s.succeeded, s.failed), (0, 0))
        s.records.append({"blob_name": "a", "pages": 1, "text_length": 1, "preview": "x"})
        s.failures.append({"blob_name": "b", "error": "bad json"})
        self.assertEqual((s.succeeded, s.failed), (1, 1))


if __name__ == "__main__":
    unittest.main()
