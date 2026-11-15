"""bangkokair/web.py 查询链路的本地测试。

使用自造的 HTML/JSON 样例和 FakeSession 模拟 HTTP 响应，
不访问真实站点，也不依赖 execjs（测试前注入 stub）。
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import types
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
WEB_PY = os.path.join(REPO_ROOT, 'bangkokair', 'web.py')
SAMPLES = os.path.join(os.path.dirname(__file__), 'samples')

BOOKING_URL = "https://www.bangkokair.com/AdTripFlow/booking"
OVERRIDE_URL = "https://flightbook.bangkokair.com/plnext/BangkokAir/Override.action"
CHECK_CLIENT_URL = "https://flightbook.bangkokair.com/qlSUmvhsKL"
PG_JS_URL = "https://flightbook.bangkokair.com/PGsamP1e9x.js"


def load_web_module():
    """从文件路径直接加载 web.py，并注入 execjs stub（避免依赖 JS 运行时）。"""
    execjs_stub = types.ModuleType('execjs')

    class _FakeCtx:
        def call(self, *args, **kwargs):
            return 'fake-proof'

    execjs_stub.compile = lambda js: _FakeCtx()
    sys.modules.setdefault('execjs', execjs_stub)

    spec = importlib.util.spec_from_file_location('bangkokair_web', WEB_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


web = load_web_module()


def read_sample(name):
    with open(os.path.join(SAMPLES, name), encoding='utf-8') as fp:
        return fp.read()


class FakeResponse:
    def __init__(self, status_code=200, text='', json_data=None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode('utf-8')
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError('No JSON data')
        return self._json_data


class FakeSession:
    """按 URL 队列依次返回预置响应的假会话。"""

    def __init__(self, routes):
        self.routes = {url: list(responses) for url, responses in routes.items()}
        self.requests = []
        self.proxies = {}

    def request(self, method, url, **kwargs):
        self.requests.append((method, url))
        queue = self.routes.get(url)
        if not queue:
            raise AssertionError('未预期的请求: %s %s' % (method, url))
        return queue.pop(0)


def make_client(routes):
    client = web.Bangkokair({'B_LOCATION': 'CNX', 'E_LOCATION': 'BKK'})
    client.session = FakeSession(routes)
    return client


def ok_routes():
    """构造一条完整成功链路的响应。"""
    booking_json = json.loads(read_sample('booking_response.json'))
    return {
        BOOKING_URL: [FakeResponse(json_data=booking_json)],
        OVERRIDE_URL: [
            FakeResponse(text=read_sample('override_page.html')),       # get_pg_js_name
            FakeResponse(text=read_sample('search_result_page.html')),  # search
        ],
        CHECK_CLIENT_URL: [FakeResponse(json_data={'status': 'ok'})],
        PG_JS_URL: [
            FakeResponse(text=read_sample('pg_js_page.html')),  # get_pid
            FakeResponse(text='ok'),                            # get_cookies
        ],
    }


class MainFlowTest(unittest.TestCase):
    def test_main_success(self):
        client = make_client(ok_routes())
        with contextlib.redirect_stdout(io.StringIO()):
            result = client.main()
        self.assertTrue(result['success'])
        self.assertIsNone(result['failed_step'])
        self.assertIsNone(result['error_type'])
        self.assertIsNone(result['error_message'])
        self.assertEqual(result['completed_steps'], list(web.Bangkokair.STEPS))
        # 解析出的航班分组与价格列表
        self.assertEqual(len(result['flights']), 2)
        self.assertEqual(result['flights'][0]['flightNumber'], 'PG215')
        self.assertEqual(len(result['prices']), 2)
        self.assertEqual(result['prices'][0]['price'], 2500)

    def test_booking_http_error(self):
        routes = ok_routes()
        routes[BOOKING_URL] = [FakeResponse(status_code=500, text='server error')]
        client = make_client(routes)
        result = client.main()
        self.assertFalse(result['success'])
        self.assertEqual(result['failed_step'], 'booking')
        self.assertEqual(result['error_type'], 'RequestError')
        self.assertIn('500', result['error_message'])
        self.assertIn('booking', result['error_message'])
        self.assertEqual(result['completed_steps'], [])

    def test_get_pg_js_name_regex_miss(self):
        routes = ok_routes()
        routes[OVERRIDE_URL][0] = FakeResponse(text=read_sample('override_page_no_pg.html'))
        client = make_client(routes)
        result = client.main()
        self.assertFalse(result['success'])
        self.assertEqual(result['failed_step'], 'get_pg_js_name')
        self.assertEqual(result['error_type'], 'RequestError')
        self.assertIn('get_pg_js_name', result['error_message'])
        self.assertEqual(result['completed_steps'], ['booking'])

    def test_get_pg_js_name_http_error(self):
        routes = ok_routes()
        routes[OVERRIDE_URL][0] = FakeResponse(status_code=403, text='forbidden')
        client = make_client(routes)
        result = client.main()
        self.assertFalse(result['success'])
        self.assertEqual(result['failed_step'], 'get_pg_js_name')
        self.assertIn('403', result['error_message'])
        self.assertIn('get_pg_js_name', result['error_message'])

    def test_get_pid_regex_miss(self):
        routes = ok_routes()
        routes[PG_JS_URL][0] = FakeResponse(text=read_sample('pg_js_page_no_pid.html'))
        client = make_client(routes)
        result = client.main()
        self.assertFalse(result['success'])
        self.assertEqual(result['failed_step'], 'get_pid')
        self.assertEqual(result['error_type'], 'RequestError')
        self.assertIn('get_pid', result['error_message'])
        self.assertEqual(result['completed_steps'],
                         ['booking', 'get_pg_js_name', 'check_client'])

    def test_search_http_error(self):
        routes = ok_routes()
        routes[OVERRIDE_URL][1] = FakeResponse(status_code=502, text='bad gateway')
        client = make_client(routes)
        result = client.main()
        self.assertFalse(result['success'])
        self.assertEqual(result['failed_step'], 'search')
        self.assertIn('502', result['error_message'])
        self.assertIn('search', result['error_message'])
        self.assertEqual(result['completed_steps'],
                         ['booking', 'get_pg_js_name', 'check_client',
                          'get_pid', 'get_cookies'])

    def test_search_parse_regex_miss(self):
        routes = ok_routes()
        routes[OVERRIDE_URL][1] = FakeResponse(
            text=read_sample('search_result_page_no_config.html'))
        client = make_client(routes)
        result = client.main()
        self.assertFalse(result['success'])
        self.assertEqual(result['failed_step'], 'search_parse')
        self.assertEqual(result['error_type'], 'RequestError')
        self.assertIn('search_parse', result['error_message'])
        self.assertEqual(result['completed_steps'],
                         ['booking', 'get_pg_js_name', 'check_client',
                          'get_pid', 'get_cookies', 'search'])


class SearchParseTest(unittest.TestCase):
    def test_parse_returns_result_and_keeps_print(self):
        client = make_client({})
        client.common_data['html'] = read_sample('search_result_page.html')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            parsed = client.search_parse()
        # 解析结果进入返回值和 common_data
        self.assertEqual(parsed['flights'], client.common_data['flights'])
        self.assertEqual(parsed['prices'], client.common_data['prices'])
        self.assertEqual(parsed['flights'][1]['flightNumber'], 'PG216')
        self.assertEqual(parsed['prices'][1]['price'], 3100)
        # 原有打印行为保留
        printed = buf.getvalue()
        self.assertIn('PG215', printed)
        self.assertIn('2500', printed)

    def test_parse_regex_miss_raises_request_error(self):
        client = make_client({})
        client.common_data['html'] = read_sample('search_result_page_no_config.html')
        with self.assertRaises(web.RequestError) as ctx:
            client.search_parse()
        self.assertEqual(ctx.exception.step, 'search_parse')


if __name__ == '__main__':
    unittest.main()
