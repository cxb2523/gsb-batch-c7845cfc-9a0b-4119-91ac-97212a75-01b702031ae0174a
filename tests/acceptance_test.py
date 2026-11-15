# -*- coding: utf-8 -*-
"""
验收测试（在 Python 3.12 下运行）。

生产代码 bili-comment/get_floor.py、bili-comment.py 是 Python 2.7 源码，
这里不改动它们的运行时语义，只在加载时用 lib2to3 做 py2->py3 语法转换，
并用假的 requests/登录/动态/提交模块替换真实网络依赖：

  1. get_floor 单测：假 requests 模拟 超时/非200/结构异常/正常，
     断言全部返回 GET_FLOOR_FAILED 标记而不是抛异常；
  2. usage00（单线程抢楼）：偶发失败后恢复、持续失败到上限、正常抢到楼层；
  3. usage01（多线程抢楼）：同样三种情形，并验证工作线程不会提前退出、
     主线程不会死等锁（所有锁最终都被释放）。
"""
import os
import re
import sys
import time
import json
import shutil
import tempfile
import unittest
import importlib.util

REPO_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bili-comment'))
WORKDIR = tempfile.mkdtemp(prefix='bili_comment_test_')

# py2 顶层 import urllib2 / import thread 在 py3 下不存在，给兼容桩
# （thread 桩直接复用 _thread，allocate_lock/start_new_thread/exit 都有）
import _thread
for _name, _mod in (('urllib2', None), ('thread', _thread)):
    if _name not in sys.modules:
        sys.modules[_name] = _mod if _mod is not None else type(sys)(_name)

_PRINT_RE = re.compile(r"^(\s*)print\s+(.*?)(,?)\s*$", re.MULTILINE)
_EXCEPT_RE = re.compile(r"except\s+([\w\.]+)\s*,\s*(\w+)\s*:")


def _py2_to_py3(src):
    """仅用于测试加载的轻量 py2->py3 语法转换：print 语句 + except E, e 语法。
    生产代码保持 Python 2.7 原样，不依赖本函数。"""
    def _print_sub(m):
        indent, body, trailing = m.group(1), m.group(2).strip(), m.group(3)
        if body == '':
            return indent + 'print()'
        if body.startswith('(') and body.endswith(')') and trailing == '':
            return indent + 'print ' + body if False else indent + 'print(' + body[1:-1] + ')'
        if trailing == ',':
            return indent + 'print(%s, end=" ")' % body
        return indent + 'print(%s)' % body
    src = _PRINT_RE.sub(_print_sub, src)
    src = _EXCEPT_RE.sub(r'except \1 as \2:', src)
    return src


def py2_source(name):
    with open(os.path.join(REPO_SRC, name), 'rb') as f:
        src = f.read().decode('utf-8')
    # 模块自带的 __main__ 自测块在测试环境无用，且含 py2 尾逗号 print，直接截掉
    main_idx = src.find("if __name__ == '__main__'")
    if main_idx != -1 and name in ('get_floor.py', 'submmit_comment.py'):
        src = src[:main_idx]
    # 测试环境里没有登录/动态/沙发这些模块，顶层导入替换成测试桩
    src = src.replace('import login_cookie', 'import login_cookie_stub as login_cookie')
    src = src.replace('import up_dynamic', 'import up_dynamic_stub as up_dynamic')
    return _py2_to_py3(src)


def load_py2(mod_name, file_name, transformed=None):
    path = os.path.join(WORKDIR, file_name)
    code = transformed if transformed is not None else py2_source(file_name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------- 测试桩 ----------
class FakeRequestException(Exception):
    pass


class FakeResponse(object):
    """py2 里 response.content 是 bytes==str，测试统一用 str 承载，
    这样生产代码里 response_floor.find('page') 这类调用在 py3 下也能跑通"""
    def __init__(self, status_code, content):
        self.status_code = status_code
        if isinstance(content, bytes):
            content = content.decode('utf-8')
        self.content = content


class FakeRequests(object):
    """requests 桩：按脚本序列返回响应或抛异常，序列耗尽后重复最后一项"""
    exceptions = type('exceptions', (), {'RequestException': FakeRequestException})

    def __init__(self):
        self.script = []
        self.calls = 0

    def get(self, url, headers=None, timeout=None):
        idx = min(self.calls, len(self.script) - 1)
        item = self.script[idx]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        status, content = item
        return FakeResponse(status, content)

    def post(self, url, data=None, headers=None):
        return FakeResponse(200, b'{"code":0,"data":{"rpid":1}}')


# ---------- get_floor 单元测试 ----------
class GetFloorTests(unittest.TestCase):
    def setUp(self):
        self.fake_requests = FakeRequests()
        sys.modules['requests'] = self.fake_requests
        self.get_floor = load_py2('get_floor_under_test', 'get_floor.py')

    def _floor_page(self, floor):
        return (200, ('{"page":{"num":1},"replies":[{"floor":%d,}]' % floor).encode('utf-8'))

    def test_request_exception_returns_marker(self):
        self.fake_requests.script = [FakeRequestException('timeout / connection reset')]
        self.assertEqual(self.get_floor.get_floor('11259766', 0),
                         self.get_floor.GET_FLOOR_FAILED)

    def test_non_200_returns_marker(self):
        self.fake_requests.script = [(502, b'bad gateway'), (404, b'not found')]
        for _ in range(2):
            self.assertEqual(self.get_floor.get_floor('11259766', 0),
                             self.get_floor.GET_FLOOR_FAILED)

    def test_unparseable_response_returns_marker(self):
        self.fake_requests.script = [
            (200, b'{"code":-404,"data":null}'),                       # 没有 page
            (200, b'{"page":{"num":1},"replies":[{"floor":abc'),      # 内容残缺
            (200, b'{"page":{"num":1},"replies":[{"floor":xyz,"rpid":1}]}'),  # 非数字
        ]
        for _ in range(3):
            self.assertEqual(self.get_floor.get_floor('11259766', 0),
                             self.get_floor.GET_FLOOR_FAILED)

    def test_success_returns_floor_string(self):
        self.fake_requests.script = [self._floor_page(60)]
        self.assertEqual(self.get_floor.get_floor('11259766', 0), '60')

    def test_zero_comments_is_not_failure(self):
        # 没有任何评论时保持旧行为：返回 '0'，不是失败标记
        self.fake_requests.script = [
            (200, b'{"page":{"num":1,"size":20},"replies":null}'),
        ]
        self.assertEqual(self.get_floor.get_floor('11259766', 0), '0')

    def test_rpid_lookup_failure_still_marker(self):
        self.fake_requests.script = [
            (200, b'{"page":{"num":1},"replies":[{"floor":7,"rpid":999}]}'),
        ]
        self.assertEqual(self.get_floor.get_floor('11259766', 123456),
                         self.get_floor.GET_FLOOR_FAILED)


# ---------- 主程序（usage00 / usage01）测试 ----------
class MainProgramTestBase(unittest.TestCase):
    SUBMIT_FLOOR = 62  # 提交评论后声称拿到的楼层

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='bili_case_')
        os.chdir(self.workdir)
        self.fake_requests = FakeRequests()
        sys.modules['requests'] = self.fake_requests

        login_stub = type(sys)('login_cookie_stub')
        login_stub.get_access_key_kaaass = lambda u, p: 'k'
        login_stub.get_cookie = lambda k: 'cookie_from_login'
        sys.modules['login_cookie_stub'] = login_stub
        dyn_stub = type(sys)('up_dynamic_stub')
        dyn_stub.up_dynamic = lambda uid, n: 1
        sys.modules['up_dynamic_stub'] = dyn_stub

        # 写一个最小 config.json，走真实的配置解析路径（含 max_get_failures）
        with open(os.path.join(self.workdir, 'config.json'), 'w') as f:
            json.dump({
                'usage': 0,
                'username': '',
                'password': '',
                'cookie': 'fake=1; bili_jct=%s;' % ('a' * 32),
                'comment': '',
                'sofa_mode': {'up_uid': '1'},
                'floor_mode': {'av_number': '11259766', 'floor': 62},
                'get_refresh': 0,
                'submmit_refresh': 0,
                'max_get_failures': 3,
                'multithread': {'floor_enabled': 0, 'thread': 4},
            }, f)

        self.get_floor_mod = load_py2('get_floor', 'get_floor.py')
        self.submit_mod = load_py2('submmit_comment', 'submmit_comment.py',
                                   transformed=py2_source('submmit_comment.py'))

        # 主程序：注入不会阻塞的 raw_input，并让 __main__ 块失效
        main_src = py2_source('bili-comment.py')
        main_src = main_src.replace(
            "if __name__ == '__main__':",
            "raw_input = lambda prompt='': ''\nif False :"
        )
        self.main = load_py2('bili_comment_main', 'bili-comment.py',
                             transformed=main_src)
        self.main.av_number = '11259766'
        self.main.floor = 62
        self.main.get_refresh = 0
        self.main.submmit_refresh = 0
        self.main.thread_total = 4
        self.main.max_get_failures = 3
        self.main.raw_input = lambda prompt='': ''

        self.failure_marker = self.get_floor_mod.GET_FLOOR_FAILED
        self.scripted_floors = []
        self._queries = [0]

        def fake_get_floor(av, rpid):
            seq = self.scripted_floors
            n = self._queries[0]
            self._queries[0] = n + 1
            if callable(seq):
                return seq(n)
            return seq[min(n, len(seq) - 1)]

        self.main.get_floor.get_floor = fake_get_floor
        self.main.submmit_comment.submmit_comment = self._fake_submit

        # 记录 floor_cycle 产生的锁，供多线程用例观测
        self.created_locks = []
        self._orig_floor_cycle = self.main.floor_cycle

        def recording_floor_cycle(av, fl, total):
            real_alloc = self.main.thread.allocate_lock

            def alloc():
                lk = real_alloc()
                self.created_locks.append(lk)
                return lk
            self.main.thread.allocate_lock = alloc
            try:
                return self._orig_floor_cycle(av, fl, total)
            finally:
                self.main.thread.allocate_lock = real_alloc

        self.main.floor_cycle = recording_floor_cycle

        self.printed = []
        import builtins
        self._real_print = builtins.print
        builtins.print = self._capture_print

    def _fake_submit(self, av_number, comment, cookie, usage_arg):
        return 's', self.SUBMIT_FLOOR

    def _capture_print(self, *args, **kwargs):
        self.printed.append(' '.join(str(a) for a in args))

    def tearDown(self):
        import builtins
        builtins.print = self._real_print
        shutil.rmtree(self.workdir, ignore_errors=True)

    def script(self, items):
        """楼层(int) 或 None(=失败标记) 组成的查询序列，耗尽后重复末项；
        也可直接传 callable(n)，按第 n 次查询动态决定结果"""
        if callable(items):
            self.scripted_floors = lambda n: self.failure_marker if items(n) is None else str(items(n))
            self._queries = [0]
            return
        def value(x):
            return self.failure_marker if x is None else str(x)
        self.scripted_floors = [value(x) for x in items]
        self._queries = [0]

    def output(self):
        return '\n'.join(self.printed)

    def assert_failed_ending(self):
        self.assertIn('[failed]', self.output())
        self.assertNotIn('I am sorry.', self.output())
        self.assertNotIn('Done.Good luck.', self.output())

    def assert_good_ending(self):
        self.assertIn('Done.Good luck.', self.output())
        self.assertNotIn('[failed]', self.output())

    def assert_sorry_ending(self):
        self.assertIn('I am sorry.', self.output())
        self.assertNotIn('[failed]', self.output())


class Usage00Tests(MainProgramTestBase):
    def test_intermittent_failure_then_recovery_succeeds(self):
        # 前两次查询失败（未到上限 3），随后恢复并成功抢到
        self.script([None, None, 50, None, 57, 58, 59, 60])
        self.main.usage00()
        self.assertIn('[warn]', self.output())
        self.assert_good_ending()
        self.assertEqual(self.main.fail_count, 0)  # 成功读到楼层后计数清零

    def test_persistent_failure_hits_limit(self):
        self.main.max_get_failures = 5
        self.script([None] * 10)
        self.main.usage00()
        self.assert_failed_ending()
        self.assertEqual(self.main.fail_count, 5)
        self.assertEqual(self._queries[0], 5)      # 达到上限即停止，不多查

    def test_normal_grab_without_any_failure(self):
        self.script([55, 56, 57, 58, 59, 60])
        self.main.usage00()
        self.assert_good_ending()
        self.assertEqual(self.main.fail_count, 0)

    def test_no_limit_keeps_polling_through_failures(self):
        # 缺省行为：max_get_failures=0，失败不终止，恢复后抢到
        self.main.max_get_failures = 0
        self.script([None, None, None, None, 58, 59, 60])
        self.main.usage00()
        self.assert_good_ending()
        self.assertIn('no limit configured', self.output())

    def test_missed_floor_prints_sorry_not_failed(self):
        # 查楼/提交都成功，但没抢到目标楼层 -> 仍是 I am sorry，不被失败结局污染
        self.SUBMIT_FLOOR = 63
        self.script([58, 59, 60])
        self.main.usage00()
        self.assert_sorry_ending()

    def test_failure_limit_boundary_reset_on_success(self):
        # 2 次失败后成功（清零），再连续失败到上限：计数应重新累计
        self.main.max_get_failures = 3
        self.script([None, None, 50, None, None, None])
        self.main.usage00()
        self.assert_failed_ending()
        self.assertEqual(self.main.fail_count, 3)


class Usage01Tests(MainProgramTestBase):
    def test_intermittent_failure_then_recovery_succeeds(self):
        # 每 3 次查询有 1 次失败，4 个工作线程并发共享同一序列
        self.script(lambda n: None if n % 3 == 0 else 55 + n)
        self.main.usage01()
        self.assert_good_ending()
        self.assertEqual(self.main.get_stop_reason(), 'success')
        # usage01 正常返回即说明所有工作线程已退出、锁全部释放
        self.assertTrue(all(not lk.locked() for lk in self.created_locks))

    def test_persistent_failure_hits_limit_and_threads_exit(self):
        self.main.max_get_failures = 4
        self.script([None] * 100)
        t0 = time.time()
        self.main.usage01()
        elapsed = time.time() - t0
        self.assert_failed_ending()
        self.assertEqual(self.main.get_stop_reason(), 'fail_limit')
        self.assertEqual(self.main.fail_count, 4)
        self.assertLess(elapsed, 10)  # 主线程没有死等
        self.assertTrue(all(not lk.locked() for lk in self.created_locks))

    def test_normal_grab_multithreaded(self):
        self.script([55, 56, 57, 58, 59, 60])
        self.main.usage01()
        self.assert_good_ending()
        self.assertEqual(self.main.get_stop_reason(), 'success')
        self.assertTrue(all(not lk.locked() for lk in self.created_locks))

    def test_no_limit_multithreaded_survives_failures(self):
        # 无上限：前 8 次全失败，之后恢复，线程必须一直存活并成功结束
        self.main.max_get_failures = 0
        self.script([None] * 8 + [57, 58, 59, 60])
        self.main.usage01()
        self.assert_good_ending()
        self.assertEqual(self.main.get_stop_reason(), 'success')

    def test_failure_does_not_release_locks_early(self):
        # 失败阶段工作线程绝不能释放锁/退出，否则主线程会误判成功
        self.main.max_get_failures = 50
        self.script([None] * 6 + [58, 59, 60])
        orig_poll = self.main.poll_floor_once

        def wrapped_poll(av):
            result = orig_poll(av)
            if self._queries[0] <= 6 and self.created_locks:
                self.assertTrue(all(lk.locked() for lk in self.created_locks))
            return result

        self.main.poll_floor_once = wrapped_poll
        self.main.usage01()
        self.assert_good_ending()
        self.assertEqual(self.main.get_stop_reason(), 'success')
        self.assertTrue(all(not lk.locked() for lk in self.created_locks))


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
