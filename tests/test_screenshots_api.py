import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from crawlbase import ScreenshotsAPI


def make_response(status_code, body=b'', headers=None):
    return {
        'status_code': status_code,
        'body': body,
        'headers': dict(headers or {}),
    }


class ScreenshotsAPISaveTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='screenshots-test-')
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.path = os.path.join(self.directory, 'shot.jpg')
        self.api = ScreenshotsAPI({'token': 'fake-token'})

    def _fake_request(self, response):
        # `request` is replaced entirely: no HTTP client and no real token is used.
        self.api.request = lambda options={}, data=None: response
        return response

    def _leftovers(self):
        return [name for name in os.listdir(self.directory) if name != 'shot.jpg']

    def test_success_writes_file_and_returns_path(self):
        with open(self.path, 'wb') as f:
            f.write(b'previous-content')
        headers = {
            'success': 'true',
            'url': 'https://example.com',
            'remaining_requests': '42',
            'screenshot_url': 'https://example.com/shot.jpg',
        }
        response = self._fake_request(make_response(200, b'\xff\xd8fresh-jpeg', headers))

        result = self.api.get('https://example.com', {'save_to_path': self.path})

        with open(self.path, 'rb') as f:
            self.assertEqual(f.read(), b'\xff\xd8fresh-jpeg')
        self.assertEqual(result['file'], self.path)
        self.assertEqual(result['status_code'], 200)
        for name, value in headers.items():
            self.assertEqual(result['headers'][name], value)
        self.assertEqual(self._leftovers(), [])

    def test_success_with_default_filename_writes_file(self):
        self._fake_request(make_response(200, b'image'))
        with mock.patch('crawlbase.screenshots_api.tempfile.gettempdir', return_value=self.directory):
            result = self.api.get('https://example.com', {})

        self.assertEqual(result['status_code'], 200)
        self.assertTrue(result['file'].endswith('.jpg'))
        self.assertEqual(os.path.dirname(result['file']), self.directory)
        with open(result['file'], 'rb') as f:
            self.assertEqual(f.read(), b'image')
        # Only the screenshot itself exists; no leftover temporary file.
        self.assertEqual(os.listdir(self.directory), [os.path.basename(result['file'])])

    def test_non_200_keeps_existing_file_and_reports_failure(self):
        with open(self.path, 'wb') as f:
            f.write(b'previous-content')
        headers = {
            'success': 'false',
            'url': 'https://example.com',
            'remaining_requests': '7',
            'screenshot_url': 'None',
        }
        self._fake_request(make_response(503, b'upstream-error', headers))

        result = self.api.get('https://example.com', {'save_to_path': self.path})

        with open(self.path, 'rb') as f:
            self.assertEqual(f.read(), b'previous-content')
        self.assertEqual(result['status_code'], 503)
        for name, value in headers.items():
            self.assertEqual(result['headers'][name], value)
        self.assertIn('failure_reason', result['headers'])
        self.assertIn('503', result['headers']['failure_reason'])
        self.assertNotIn('file', result)
        self.assertEqual(self._leftovers(), [])

    def test_non_200_without_saved_file_does_not_create_one(self):
        self._fake_request(make_response(500, b''))

        result = self.api.get('https://example.com', {'save_to_path': self.path})

        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(result['status_code'], 500)
        self.assertIn('failure_reason', result['headers'])
        # The documented headers stay present even when the HTTP error carries none.
        for name in ('success', 'url', 'remaining_requests', 'screenshot_url'):
            self.assertIn(name, result['headers'])
        self.assertEqual(self._leftovers(), [])

    def test_default_filename_failure_leaves_no_file(self):
        self._fake_request(make_response(502, b''))
        with mock.patch('crawlbase.screenshots_api.tempfile.gettempdir', return_value=self.directory), \
                mock.patch('crawlbase.screenshots_api.uuid.uuid4', return_value='fixed-name'):
            result = self.api.get('https://example.com', {})

        self.assertEqual(result['status_code'], 502)
        self.assertNotIn('file', result)
        self.assertEqual(os.listdir(self.directory), [])

    def test_replace_failure_cleans_temporary_file(self):
        with open(self.path, 'wb') as f:
            f.write(b'previous-content')
        self._fake_request(make_response(200, b'fresh'))

        with mock.patch('crawlbase.screenshots_api.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaises(OSError):
                self.api.get('https://example.com', {'save_to_path': self.path})

        with open(self.path, 'rb') as f:
            self.assertEqual(f.read(), b'previous-content')
        self.assertEqual(self._leftovers(), [])

    def test_concurrent_saves_never_leave_partial_file(self):
        payloads = [bytes([65 + index]) * 200000 for index in range(8)]
        barrier = threading.Barrier(len(payloads))
        errors = []

        def save(payload):
            api = ScreenshotsAPI({'token': 'fake-token'})
            api.request = lambda options={}, data=None: make_response(200, payload)
            try:
                barrier.wait()
                api.get('https://example.com', {'save_to_path': self.path})
            except Exception as error:  # pragma: no cover - reported below
                errors.append(error)

        threads = [threading.Thread(target=save, args=(payload,)) for payload in payloads]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        with open(self.path, 'rb') as f:
            self.assertIn(f.read(), payloads)
        self.assertEqual(self._leftovers(), [])


if __name__ == '__main__':
    unittest.main()
