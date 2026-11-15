"""用真实 Node 驱动一次 execjs 路径，验证 xyz 与 JS 实际执行结果一致。

不访问真实站点，也不需要安装 execjs 包：这里提供一个最小 execjs 兼容垫片，
真正执行签名 JS 的是本机 Node。若本机没有 Node 则跳过。
"""
import hashlib
import importlib.util
import pathlib
import subprocess
import sys

import pytest

CASE = pathlib.Path(__file__).parent
NODE_CANDIDATES = [
    'node',
    r'C:\Program Files\nodejs\node.exe',
    r'C:\Program Files (x86)\nodejs\node.exe',
]


def _find_node():
    for candidate in NODE_CANDIDATES:
        try:
            subprocess.run([candidate, '--version'], capture_output=True, check=True)
            return candidate
        except (OSError, subprocess.CalledProcessError):
            continue
    return None


NODE = _find_node()
pytestmark = pytest.mark.skipif(NODE is None, reason='本机没有可用的 Node')


def _load_module():
    spec = importlib.util.spec_from_file_location('xb_rank_362_e2e', CASE / 'js3.6.2.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NodeContext:
    """最小 execjs 兼容垫片：把表达式交给 Node 求值。"""

    def __init__(self, source):
        self.source = source

    def eval(self, expression):
        script = self.source + '\nprocess.stdout.write(String(' + expression + '));'
        return subprocess.run([NODE, '-e', script], capture_output=True, text=True,
                              encoding='utf-8', check=True).stdout


class NodeExecjsShim:
    def compile(self, source):
        return NodeContext(source)


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def json(self):
        return {'ok': True}


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url=None, headers=None, data=None, json=None):
        self.calls.append({'url': url, 'data': data, 'json': json})
        return FakeResponse('{"code": 200}')


def test_real_node_execjs_path_matches_python_fallback(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, 'execjs', NodeExecjsShim())

    rank = module.XbRank(session=FakeSession())
    path = 'b("/nr/bili/rank/complexMainRank?")'
    nonce, xyz_js, source = rank.getxyz(path)

    assert source == module.SIGN_SOURCE_EXECJS
    assert len(nonce) == 9
    # 与纯 Python 回退在同一段文本上算出的结果完全一致
    assert xyz_js == module._python_xyz(module._extract_signed_text(
        path.replace(module._NONCE_PLACEHOLDER, nonce)))
    assert xyz_js == hashlib.md5(b'/nr/bili/rank/complexMainRank?').hexdigest()


def test_real_node_signed_request_url_is_consistent(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, 'execjs', NodeExecjsShim())
    session = FakeSession()
    rank = module.XbRank(session=session)

    result = rank.getRank4()

    assert result.sign_source == module.SIGN_SOURCE_EXECJS
    url = session.calls[-1]['url']
    nonce = url.split('nonce=')[1].split('&')[0]
    xyz = url.split('xyz=')[1]
    assert len(nonce) == 9
    assert xyz == hashlib.md5(b'/xdnphb/nr/cloud/ks/mini/rank/accountAllRankList?').hexdigest()
