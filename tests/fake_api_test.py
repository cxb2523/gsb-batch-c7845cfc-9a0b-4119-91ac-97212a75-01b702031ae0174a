# -*- coding: utf-8 -*-
"""本地假接口端到端验证 get_floor 的三种失败路径与成功路径（不访问外网）。
用 http.server 起本地服务，按路径返回 超时/500/坏JSON/正常 四种响应，
再把 get_floor.py 的请求 URL 重写到本地地址后加载执行。"""
import os
import re
import sys
import time
import json
import threading
import http.server
import tempfile
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, '..', 'bili-comment', 'get_floor.py'))

NORMAL_BODY = '{"code":0,"data":{"page":{"num":1,"size":20},"replies":[{"rpid":1,"floor":58,}]}}'

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, code, body=b''):
        try:
            self.send_response(code)
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # 客户端超时主动断开，属于测试预期，忽略服务端噪音

    def do_GET(self):
        if self.path.startswith('/timeout'):
            time.sleep(3)  # 配合客户端 timeout=1 触发请求异常
            self._reply(200, b'late')
        elif self.path.startswith('/http500'):
            self._reply(500, b'server error')
        elif self.path.startswith('/badjson'):
            self._reply(200, b'{"code":-404,"data":null}')
        else:
            self._reply(200, NORMAL_BODY.encode('utf-8'))

server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

# 轻量 py2->py3 转换（print 语句），并把请求地址替换成本地假接口、超时缩短到 1s
src = open(SRC, encoding='utf-8').read()
src = src[:src.find("if __name__ == '__main__'")]
src = re.sub(r"^(\s*)print\s+(.*?)(,?)\s*$", lambda m:
             m.group(1) + 'print(' + m.group(2) + (', end=" ")' if m.group(3) else ')'),
             src, flags=re.MULTILINE)
src = src.replace('timeout = 20', 'timeout = 1')
src = src.replace(
    "av_url = 'http://api.bilibili.com/x/reply?oid=' + av_number + '&type=1&pn=1'",
    "av_url = 'http://127.0.0.1:%d/' + av_number" % port)

work = tempfile.mkdtemp(prefix='fake_api_')
path = os.path.join(work, 'get_floor_py3.py')
# py3 下 requests 返回 bytes，而 py2 里 bytes==str；测试加载层归一化为 str
src = src.replace(
    'response_floor = get_floor_res.content',
    "response_floor = get_floor_res.content\n"
    "    if isinstance(response_floor, bytes): response_floor = response_floor.decode('utf-8')")
open(path, 'w', encoding='utf-8').write(src)
sys.modules.setdefault('urllib2', type(sys)('urllib2'))  # py3 无此模块，生产代码也未实际使用
spec = importlib.util.spec_from_file_location('get_floor_fakeapi', path)
gf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gf)

results = []
def check(name, av, expect):
    got = gf.get_floor(av, 0)
    ok = got == expect
    results.append(ok)
    print('%-28s got=%-4r expect=%-4r %s' % (name, got, expect, 'PASS' if ok else 'FAIL'))

check('request timeout', 'timeout', gf.GET_FLOOR_FAILED)
check('http 500', 'http500', gf.GET_FLOOR_FAILED)
check('unparseable body', 'badjson', gf.GET_FLOOR_FAILED)
check('normal floor', 'normal', '58')

server.shutdown()
sys.exit(0 if all(results) else 1)
