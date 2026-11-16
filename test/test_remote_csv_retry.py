"""Tests for retryable reads of remote csv sources (http/https/ftp).

All tests use a local HTTP server or an injected read function, no external network access.
"""

import http.server
import logging
import threading
import urllib.error

import pandas as pd
import pytest
from click.testing import CliRunner

import etl


CSV_BODY = b'name;value\nalpha;1\nbeta;2\n'


class _CsvHandler(http.server.BaseHTTPRequestHandler):
    # mutable per-test state, configured by the fixture
    statuses = [200]

    def do_GET(self):
        handler = type(self)
        status = handler.statuses.pop(0) if len(handler.statuses) > 1 else handler.statuses[0]
        handler.requests.append(self.path)
        if status == 200:
            self.send_response(200)
            self.send_header('Content-Type', 'text/csv')
            self.send_header('Content-Length', str(len(CSV_BODY)))
            self.end_headers()
            self.wfile.write(CSV_BODY)
        else:
            self.send_response(status)
            self.send_header('Content-Length', '3')
            self.end_headers()
            self.wfile.write(b'err')

    def log_message(self, *args):
        pass


class _Server:
    def __init__(self, statuses):
        self.requests = []
        handler = type('Handler', (_CsvHandler,), {'statuses': list(statuses), 'requests': self.requests})
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.httpd.server_port}/data.csv'

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def make_server():
    servers = []

    def factory(*statuses):
        server = _Server(statuses or (200,))
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.stop()


@pytest.fixture(autouse=True)
def clean_logging():
    """etl.cli configures the root logger; isolate handlers between tests."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
    root.setLevel(level)


def invoke(*args):
    return CliRunner().invoke(etl.cli, list(args))


class TestSuccessfulRetry:
    def test_retry_after_server_errors_then_success(self, make_server, caplog):
        server = make_server(500, 503, 200)
        result = invoke('--source', f'{server.url}??retries=3&retry_delay=0')

        assert result.exit_code == 0, result.output
        assert len(server.requests) == 3
        assert 'alpha;1' in result.stdout
        assert 'beta;2' in result.stdout

        messages = [record.getMessage() for record in caplog.records]
        assert any('retry 1/3' in message and 'HTTP Error 500' in message for message in messages)
        assert any('retry 2/3' in message and 'HTTP Error 503' in message for message in messages)

    def test_retry_logs_attempt_wait_and_reason(self, make_server, caplog, monkeypatch):
        server = make_server(500, 200)
        delays = []
        monkeypatch.setattr(etl.time, 'sleep', lambda seconds: delays.append(seconds))
        result = invoke('--source', f'{server.url}??retries=2&retry_delay=7')

        assert result.exit_code == 0, result.output
        assert delays == [7]
        retry_records = [record for record in caplog.records if 'retry 1/2' in record.getMessage()]
        assert len(retry_records) == 1
        message = retry_records[0].getMessage()
        assert 'in 7s' in message
        assert 'Internal Server Error' in message
        assert retry_records[0].levelno == logging.WARNING

    def test_successful_retry_continues_to_target_and_transform(self, make_server, tmp_path, caplog):
        server = make_server(500, 200)
        target = tmp_path / 'result.csv'
        transform = tmp_path / 'transform.sql'
        transform.write_text('select name, value * 10 as value from dataset', encoding='utf-8')

        result = invoke(
            '--source', f'{server.url}??retries=1&retry_delay=0',
            '--transform', str(transform),
            '--target', str(target),
        )

        assert result.exit_code == 0, result.output
        assert target.exists()
        loaded = pd.read_csv(target, sep=';')
        assert loaded['value'].tolist() == [10, 20]

    def test_retry_params_recognized_in_debug_log(self, make_server, caplog, monkeypatch):
        server = make_server(500, 200)
        monkeypatch.setattr(etl.time, 'sleep', lambda seconds: None)
        caplog.set_level(logging.DEBUG)
        result = invoke('--source', f'{server.url}??retries=4&retry_delay=1', '--debug')

        assert result.exit_code == 0, result.output
        messages = '\n'.join(record.getMessage() for record in caplog.records)
        assert 'retries=4' in messages
        assert 'retry_delay=1' in messages

    def test_default_retry_params_are_used(self, make_server, monkeypatch):
        server = make_server(500, 500, 200)
        delays = []
        monkeypatch.setattr(etl.time, 'sleep', lambda seconds: delays.append(seconds))

        result = invoke('--source', server.url)

        assert result.exit_code == 0, result.output
        assert len(server.requests) == 3  # first attempt + default retries
        assert delays == [etl.remote_csv_retry_defaults['retry_delay']] * 2


class TestRetriesExhausted:
    def test_all_retries_exhausted_exits_nonzero(self, make_server, caplog):
        server = make_server(500)
        result = invoke('--source', f'{server.url}??retries=2&retry_delay=0')

        assert result.exit_code != 0
        assert len(server.requests) == 3
        messages = [record.getMessage() for record in caplog.records]
        assert any('retry 1/2' in message for message in messages)
        assert any('retry 2/2' in message for message in messages)
        assert any('after 3 attempts' in message for message in messages)
        assert any(record.levelno == logging.ERROR for record in caplog.records)

    def test_half_data_not_written_to_target(self, make_server, tmp_path):
        server = make_server(500)
        target = tmp_path / 'result.csv'
        result = invoke(
            '--source', f'{server.url}??retries=1&retry_delay=0',
            '--target', str(target),
        )

        assert result.exit_code != 0
        assert not target.exists()

    def test_zero_retries_single_attempt(self, make_server):
        server = make_server(500)
        result = invoke('--source', f'{server.url}??retries=0&retry_delay=0')

        assert result.exit_code != 0
        assert len(server.requests) == 1


class TestNonRetryableErrors:
    def test_http_404_not_retried(self, make_server, caplog):
        server = make_server(404)
        result = invoke('--source', f'{server.url}??retries=3&retry_delay=0')

        assert result.exit_code != 0
        assert len(server.requests) == 1
        messages = [record.getMessage() for record in caplog.records]
        assert any('not retryable' in message for message in messages)
        assert not any('retry 1/3' in message for message in messages)

    def test_invalid_retry_parameter_not_retried(self, make_server):
        server = make_server(200)
        result = invoke('--source', f'{server.url}??retries=abc')

        assert result.exit_code != 0
        assert server.requests == []

    def test_invalid_retry_delay_not_retried(self, make_server):
        server = make_server(200)
        result = invoke('--source', f'{server.url}??retry_delay=-1')

        assert result.exit_code != 0
        assert server.requests == []

    def test_parse_error_not_retried(self, make_server):
        server = make_server(200)
        # an invalid pandas parameter is a deterministic failure, it must not be retried
        result = invoke('--source', f'{server.url}??retries=3&retry_delay=0&nrows=not_a_number')

        assert result.exit_code != 0
        assert len(server.requests) == 1


class TestNonRemoteSourcesUnaffected:
    def test_local_csv_read_unchanged(self, tmp_path, caplog):
        local = tmp_path / 'local.csv'
        local.write_text('name;value\nalpha;1\n', encoding='utf-8')
        result = invoke('--source', str(local))

        assert result.exit_code == 0, result.output
        assert 'alpha;1' in result.stdout
        messages = [record.getMessage() for record in caplog.records]
        assert not any('retry' in message for message in messages)


class TestReadRemoteCsvUnit:
    def test_injected_reader_retries_then_succeeds(self, monkeypatch):
        calls = []
        delays = []
        expected = pd.DataFrame({'name': ['alpha'], 'value': [1]})

        def reader(source, **params):
            calls.append(source)
            if len(calls) < 3:
                raise urllib.error.URLError('temporary failure in name resolution')
            return expected

        monkeypatch.setattr(etl.time, 'sleep', lambda seconds: delays.append(seconds))

        dataset = etl.read_remote_csv(
            'https://example.com/data.csv',
            {'sep': ';', 'header': 0, 'retries': 2, 'retry_delay': 1},
            read_method=reader,
        )

        assert dataset is expected
        assert len(calls) == 3
        assert delays == [1, 1]

    def test_injected_reader_deterministic_error_not_retried(self, monkeypatch):
        calls = []
        delays = []

        def reader(source, **params):
            calls.append(source)
            raise FileNotFoundError('file not found')

        monkeypatch.setattr(etl.time, 'sleep', lambda seconds: delays.append(seconds))

        with pytest.raises(etl.RemoteCsvReadError):
            etl.read_remote_csv('ftp://example.com/data.csv', {'retries': 5}, read_method=reader)

        assert len(calls) == 1
        assert delays == []

    def test_retry_params_are_not_forwarded_to_reader(self):
        received = {}

        def reader(source, **params):
            received.update(params)
            return pd.DataFrame()

        etl.read_remote_csv(
            'https://example.com/data.csv',
            {'sep': ',', 'retries': 1, 'retry_delay': 0},
            read_method=reader,
        )

        assert received == {'sep': ','}


class TestRetryableClassification:
    @pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
    def test_recoverable_http_statuses(self, status):
        error = urllib.error.HTTPError('https://example.com/x.csv', status, 'error', {}, None)
        assert etl.is_retryable_remote_csv_error(error) is True

    @pytest.mark.parametrize('status', [400, 401, 403, 404])
    def test_deterministic_http_statuses(self, status):
        error = urllib.error.HTTPError('https://example.com/x.csv', status, 'error', {}, None)
        assert etl.is_retryable_remote_csv_error(error) is False

    def test_connection_errors_are_retryable(self):
        assert etl.is_retryable_remote_csv_error(urllib.error.URLError(ConnectionRefusedError(111, 'refused'))) is True
        assert etl.is_retryable_remote_csv_error(TimeoutError('timed out')) is True

    def test_ftp_permanent_error_is_not_retryable(self):
        import ftplib
        error = urllib.error.URLError(ftplib.error_perm('550 No such file'))
        assert etl.is_retryable_remote_csv_error(error) is False

    def test_local_errors_are_not_retryable(self):
        assert etl.is_retryable_remote_csv_error(ValueError('bad parameter')) is False
        assert etl.is_retryable_remote_csv_error(FileNotFoundError('missing')) is False
