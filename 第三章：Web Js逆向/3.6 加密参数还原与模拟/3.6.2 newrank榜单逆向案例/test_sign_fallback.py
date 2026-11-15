"""XbRank 签名降级逻辑测试。

全部使用本地假 session / 打桩的 execjs，不访问真实站点。
"xyz 与 JS 一致" 通过由真实 Node 执行原 JS 得到的 golden 向量校验，
因此测试本身的运行不依赖 Node 或 execjs。
"""
import hashlib
import importlib.util
import pathlib
import types

import pytest

MODULE_PATH = pathlib.Path(__file__).with_name('js3.6.2.py')


def _load_module():
    spec = importlib.util.spec_from_file_location('xb_rank_362', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


xb = _load_module()


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, text='{"ok": true}'):
        self.text = text
        self.status_code = 200
        self.url = None

    def json(self):
        return {'ok': True, 'echo': self.text}


class FakeSession:
    """记录请求，不发起任何网络调用。"""

    def __init__(self):
        self.calls = []

    def post(self, url=None, headers=None, data=None, json=None):
        self.calls.append({'url': url, 'headers': dict(headers or {}), 'data': data, 'json': json})
        return FakeResponse()


class FakeJsContext:
    """模拟 execjs 编译结果：b(path) 用与 JS 等价的 MD5 计算。"""

    def __init__(self, nonce='0a1b2c3d4'):
        self.nonce = nonce
        self.evaluated = []

    def eval(self, expression):
        self.evaluated.append(expression)
        if expression == 'nonce()':
            return self.nonce
        if expression.startswith('b('):
            # 与真实 JS 一样，b() 收到的是引号内的字符串
            return hashlib.md5(xb._extract_signed_text(expression).encode('utf-8')).hexdigest()
        raise AssertionError('unexpected eval: ' + expression)


def _fake_execjs(context=None, error=None):
    module = types.SimpleNamespace()
    module.compiled = []

    def compile_js(source):
        if error is not None:
            raise error
        assert 'function nonce()' in source and 'function b(a)' in source
        module.compiled.append(source)
        return context if context is not None else FakeJsContext()

    module.compile = compile_js
    return module


@pytest.fixture
def rank():
    return xb.XbRank(session=FakeSession())


# ---------------------------------------------------------------------------
# 1. execjs 正常时来源为 execjs
# ---------------------------------------------------------------------------
def test_execjs_available_reports_execjs(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', _fake_execjs())
    nonce, xyz, source = rank.getxyz('b("/xdnphb/data/weixinuser/getMainIndexRank?")')

    assert source == xb.SIGN_SOURCE_EXECJS
    assert nonce == '0a1b2c3d4'
    assert xyz == hashlib.md5(b'/xdnphb/data/weixinuser/getMainIndexRank?').hexdigest()
    assert rank.last_sign_source == xb.SIGN_SOURCE_EXECJS


def test_all_rank_methods_report_execjs(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', _fake_execjs())

    for method in (rank.getMedia, rank.getRank1, rank.getRank2, rank.getRank4, rank.getRank5):
        result = method()
        assert isinstance(result, xb.RankResult)
        assert result.sign_source == xb.SIGN_SOURCE_EXECJS
        assert result.data


# ---------------------------------------------------------------------------
# 2. execjs 抛错时回退 python，xyz 与 JS 一致
# ---------------------------------------------------------------------------
# 由真实 Node 执行原 JS b() 得到的输出（见任务验证记录），作为等价性基准
JS_MD5_VECTORS = [
    ('/xdnphb/index/getMedia?AppKey=joker&keyword=&pageNumber=1&pageSize=10&nonce=abcdef012',
     'f2aa513b977e56eb3a411ba2c2200154'),
    ('/xdnphb/data/weixinuser/getMainIndexRank?', '67f96eb2f8d4f47455aa2671126e7f5a'),
    ('/xdnphb/main/v1/day/rank?AppKey=joker&end=2022-01-17&rank_name=百科&rank_name_group=生活'
     '&start=2022-01-17&nonce=012345678', 'dada42972f4eafe37c85eac079930679'),
    ('/nr/bili/rank/complexMainRank?', 'b140ce51ecc8c022df2e1df68b04018d'),
    ('', 'd41d8cd98f00b204e9800998ecf8427e'),
    ('a' * 55, 'ef1772b6dff9a122358552954ad0df65'),
    ('a' * 56, '3b0c8ac703f828b04c6c197006d17218'),
    ('a' * 64, '014842d480b571495a4a0363793f7367'),
    ('a' * 65, 'c743a45e0d2e6a95cb859adae0248435'),
]


@pytest.mark.parametrize('text,expected', JS_MD5_VECTORS)
def test_python_xyz_matches_js_golden_vectors(text, expected):
    assert xb._python_xyz(text) == expected


def test_execjs_error_falls_back_to_python(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', _fake_execjs(error=RuntimeError('node not found')))

    path = 'b("/xdnphb/data/weixinuser/getMainIndexRank?")'
    nonce, xyz, source = rank.getxyz(path)

    assert source == xb.SIGN_SOURCE_PYTHON
    assert len(nonce) == 9 and all(c in '0123456789abcdef' for c in nonce)
    assert xyz == hashlib.md5(('/xdnphb/data/weixinuser/getMainIndexRank?').encode('utf-8')).hexdigest()
    assert rank.last_sign_source == xb.SIGN_SOURCE_PYTHON


def test_execjs_missing_falls_back_to_python(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', None)

    _, xyz, source = rank.getxyz('b("/nr/bili/rank/complexMainRank?")')

    assert source == xb.SIGN_SOURCE_PYTHON
    assert xyz == 'b140ce51ecc8c022df2e1df68b04018d'


def test_execjs_bad_output_falls_back_to_python(monkeypatch, rank):
    # Node 存在但返回了不合法的 nonce：同样应回退，而不是把坏值发出去
    monkeypatch.setattr(xb, 'execjs', _fake_execjs(context=FakeJsContext(nonce='XYZ')))

    nonce, _, source = rank.getxyz('b("/nr/bili/rank/complexMainRank?")')

    assert source == xb.SIGN_SOURCE_PYTHON
    assert nonce != 'XYZ'


def test_extract_signed_text_unwraps_b_expression():
    assert xb._extract_signed_text('b("/x?nonce=abc")') == '/x?nonce=abc'
    assert xb._extract_signed_text('  b("/x?a=1&b=2")  ') == '/x?a=1&b=2'
    # 非 b("...") 形式按原样使用，避免误伤
    assert xb._extract_signed_text('/plain/path') == '/plain/path'


def test_python_fallback_signs_same_text_as_js(monkeypatch, rank):
    """回退路径必须对 b() 引号内的文本做 MD5，而不是把整个表达式算进去。"""
    monkeypatch.setattr(xb, 'execjs', _fake_execjs(error=RuntimeError('no node')))

    path = 'b("/xdnphb/index/getMedia?AppKey=joker&keyword=&pageNumber=1&pageSize=10&nonce=*****")'
    nonce, xyz, source = rank.getxyz(path)

    assert source == xb.SIGN_SOURCE_PYTHON
    signed_text = path[3:-2].replace('*****', nonce)
    assert xyz == hashlib.md5(signed_text.encode('utf-8')).hexdigest()


def test_signed_requests_carry_nonce_and_xyz(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', None)

    result = rank.getRank5()

    assert result.sign_source == xb.SIGN_SOURCE_PYTHON
    call = rank.session.calls[-1]
    assert 'nonce=' in call['url'] and 'xyz=' in call['url']
    nonce = call['url'].split('nonce=')[1].split('&')[0]
    xyz = call['url'].split('xyz=')[1]
    assert len(nonce) == 9
    assert xyz == hashlib.md5('/nr/bili/rank/complexMainRank?'.encode('utf-8')).hexdigest()


def test_form_body_rank_includes_nonce_and_xyz(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', None)

    result = rank.getMedia(page=2)

    assert result.sign_source == xb.SIGN_SOURCE_PYTHON
    body = rank.session.calls[-1]['data']
    assert body['pageNumber'] == '2'
    assert len(body['nonce']) == 9
    assert len(body['xyz']) == 32


# ---------------------------------------------------------------------------
# 3. getRank3 不依赖签名，execjs 不可用时照常返回
# ---------------------------------------------------------------------------
def test_getrank3_succeeds_without_signing(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', None)

    def boom(*args, **kwargs):
        raise AssertionError('getRank3 must not attempt signing')

    monkeypatch.setattr(rank, 'getxyz', boom)

    result = rank.getRank3()

    assert result.data
    assert result.sign_source == xb.SIGN_SOURCE_NONE
    call = rank.session.calls[-1]
    assert call['headers']['n-token'] == xb.XbRank.N_TOKEN
    assert 'nonce' not in call['url'] and 'xyz' not in call['url']


def test_getrank3_does_not_pollute_shared_headers(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', None)
    before = dict(rank.headers)

    rank.getRank3()

    assert rank.headers == before
    assert 'n-token' not in rank.headers


def test_getrank3_clears_previous_sign_source(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', None)
    rank.getxyz('b("/nr/bili/rank/complexMainRank?")')
    assert rank.last_sign_source == xb.SIGN_SOURCE_PYTHON

    result = rank.getRank3()

    assert rank.last_sign_source == xb.SIGN_SOURCE_NONE
    assert result.sign_source == xb.SIGN_SOURCE_NONE


def test_rank_result_unpacks_like_tuple(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', None)

    data, source = rank.getRank2()

    assert data and source == xb.SIGN_SOURCE_PYTHON
    assert rank.getRank2()[1] == xb.SIGN_SOURCE_PYTHON


# ---------------------------------------------------------------------------
# 4. 两种签名都失败 -> 抛异常（而非空字符串）
# ---------------------------------------------------------------------------
def test_both_signers_fail_raises_sign_error(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', _fake_execjs(error=RuntimeError('node not found')))

    def broken_python_sign(path):
        raise ValueError('hashlib unavailable')

    monkeypatch.setattr(xb, '_python_sign', broken_python_sign)

    with pytest.raises(xb.SignError) as excinfo:
        rank.getRank1()

    message = str(excinfo.value)
    assert 'node not found' in message
    assert 'hashlib unavailable' in message
    # 没有发出请求，也没有返回空字符串
    assert rank.session.calls == []


def test_sign_error_propagates_from_rank_method(monkeypatch, rank):
    monkeypatch.setattr(xb, 'execjs', None)
    monkeypatch.setattr(xb, '_python_sign', lambda path: (_ for _ in ()).throw(OSError('no entropy')))

    with pytest.raises(xb.SignError, match='no entropy'):
        rank.getRank2()
