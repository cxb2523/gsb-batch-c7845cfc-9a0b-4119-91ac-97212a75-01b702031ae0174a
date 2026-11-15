import hashlib
import json
import logging
import random
from typing import Any, NamedTuple

import requests

try:  # execjs 依赖本机 JS 运行时（通常是 Node）；缺失时降级为纯 Python 实现
    import execjs
except Exception:  # pragma: no cover - 取决于运行环境
    execjs = None

logger = logging.getLogger(__name__)

# 签名来源标记，调用方据此区分本次 nonce/xyz 由谁计算
SIGN_SOURCE_EXECJS = 'execjs'
SIGN_SOURCE_PYTHON = 'python'
SIGN_SOURCE_NONE = 'none'

_NONCE_PLACEHOLDER = '*****'
_NONCE_HEX_CHARS = '0123456789abcdef'
_NONCE_LENGTH = 9

# 原站 JS：nonce() 生成 9 位十六进制随机串，b(path) 对字符串做 MD5 并输出小写十六进制
_SIGN_JS = r"""
         function nonce() {
        for (var a = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f"], b = 0; b < 500; b++)
            for (var c = "", d = 0; d < 9; d++) {
                var e = Math.floor(16 * Math.random());
                c += a[e]
            }
        return c
    }

function b(a) {
        function b(a) {
            return d(c(e(a)))
        }
        function c(a) {
            return g(h(f(a), 8 * a.length))
        }
        function d(a) {
            for (var b, c = p ? "0123456789ABCDEF" : "0123456789abcdef", d = "", e = 0; e < a.length; e++)
                b = a.charCodeAt(e),
                d += c.charAt(b >>> 4 & 15) + c.charAt(15 & b);
            return d
        }
        function e(a) {
            for (var b, c, d = "", e = -1; ++e < a.length; )
                b = a.charCodeAt(e),
                c = e + 1 < a.length ? a.charCodeAt(e + 1) : 0,
                55296 <= b && b <= 56319 && 56320 <= c && c <= 57343 && (b = 65536 + ((1023 & b) << 10) + (1023 & c),
                e++),
                b <= 127 ? d += String.fromCharCode(b) : b <= 2047 ? d += String.fromCharCode(192 | b >>> 6 & 31, 128 | 63 & b) : b <= 65535 ? d += String.fromCharCode(224 | b >>> 12 & 15, 128 | b >>> 6 & 63, 128 | 63 & b) : b <= 2097151 && (d += String.fromCharCode(240 | b >>> 18 & 7, 128 | b >>> 12 & 63, 128 | b >>> 6 & 63, 128 | 63 & b));
            return d
        }
        function f(a) {
            for (var b = Array(a.length >> 2), c = 0; c < b.length; c++)
                b[c] = 0;
            for (var c = 0; c < 8 * a.length; c += 8)
                b[c >> 5] |= (255 & a.charCodeAt(c / 8)) << c % 32;
            return b
        }
        function g(a) {
            for (var b = "", c = 0; c < 32 * a.length; c += 8)
                b += String.fromCharCode(a[c >> 5] >>> c % 32 & 255);
            return b
        }
        function h(a, b) {
            a[b >> 5] |= 128 << b % 32,
            a[14 + (b + 64 >>> 9 << 4)] = b;
            for (var c = 1732584193, d = -271733879, e = -1732584194, f = 271733878, g = 0; g < a.length; g += 16) {
                var h = c
                  , i = d
                  , o = e
                  , p = f;
                c = j(c, d, e, f, a[g + 0], 7, -680876936),
                f = j(f, c, d, e, a[g + 1], 12, -389564586),
                e = j(e, f, c, d, a[g + 2], 17, 606105819),
                d = j(d, e, f, c, a[g + 3], 22, -1044525330),
                c = j(c, d, e, f, a[g + 4], 7, -176418897),
                f = j(f, c, d, e, a[g + 5], 12, 1200080426),
                e = j(e, f, c, d, a[g + 6], 17, -1473231341),
                d = j(d, e, f, c, a[g + 7], 22, -45705983),
                c = j(c, d, e, f, a[g + 8], 7, 1770035416),
                f = j(f, c, d, e, a[g + 9], 12, -1958414417),
                e = j(e, f, c, d, a[g + 10], 17, -42063),
                d = j(d, e, f, c, a[g + 11], 22, -1990404162),
                c = j(c, d, e, f, a[g + 12], 7, 1804603682),
                f = j(f, c, d, e, a[g + 13], 12, -40341101),
                e = j(e, f, c, d, a[g + 14], 17, -1502002290),
                d = j(d, e, f, c, a[g + 15], 22, 1236535329),
                c = k(c, d, e, f, a[g + 1], 5, -165796510),
                f = k(f, c, d, e, a[g + 6], 9, -1069501632),
                e = k(e, f, c, d, a[g + 11], 14, 643717713),
                d = k(d, e, f, c, a[g + 0], 20, -373897302),
                c = k(c, d, e, f, a[g + 5], 5, -701558691),
                f = k(f, c, d, e, a[g + 10], 9, 38016083),
                e = k(e, f, c, d, a[g + 15], 14, -660478335),
                d = k(d, e, f, c, a[g + 4], 20, -405537848),
                c = k(c, d, e, f, a[g + 9], 5, 568446438),
                f = k(f, c, d, e, a[g + 14], 9, -1019803690),
                e = k(e, f, c, d, a[g + 3], 14, -187363961),
                d = k(d, e, f, c, a[g + 8], 20, 1163531501),
                c = k(c, d, e, f, a[g + 13], 5, -1444681467),
                f = k(f, c, d, e, a[g + 2], 9, -51403784),
                e = k(e, f, c, d, a[g + 7], 14, 1735328473),
                d = k(d, e, f, c, a[g + 12], 20, -1926607734),
                c = l(c, d, e, f, a[g + 5], 4, -378558),
                f = l(f, c, d, e, a[g + 8], 11, -2022574463),
                e = l(e, f, c, d, a[g + 11], 16, 1839030562),
                d = l(d, e, f, c, a[g + 14], 23, -35309556),
                c = l(c, d, e, f, a[g + 1], 4, -1530992060),
                f = l(f, c, d, e, a[g + 4], 11, 1272893353),
                e = l(e, f, c, d, a[g + 7], 16, -155497632),
                d = l(d, e, f, c, a[g + 10], 23, -1094730640),
                c = l(c, d, e, f, a[g + 13], 4, 681279174),
                f = l(f, c, d, e, a[g + 0], 11, -358537222),
                e = l(e, f, c, d, a[g + 3], 16, -722521979),
                d = l(d, e, f, c, a[g + 6], 23, 76029189),
                c = l(c, d, e, f, a[g + 9], 4, -640364487),
                f = l(f, c, d, e, a[g + 12], 11, -421815835),
                e = l(e, f, c, d, a[g + 15], 16, 530742520),
                d = l(d, e, f, c, a[g + 2], 23, -995338651),
                c = m(c, d, e, f, a[g + 0], 6, -198630844),
                f = m(f, c, d, e, a[g + 7], 10, 1126891415),
                e = m(e, f, c, d, a[g + 14], 15, -1416354905),
                d = m(d, e, f, c, a[g + 5], 21, -57434055),
                c = m(c, d, e, f, a[g + 12], 6, 1700485571),
                f = m(f, c, d, e, a[g + 3], 10, -1894986606),
                e = m(e, f, c, d, a[g + 10], 15, -1051523),
                d = m(d, e, f, c, a[g + 1], 21, -2054922799),
                c = m(c, d, e, f, a[g + 8], 6, 1873313359),
                f = m(f, c, d, e, a[g + 15], 10, -30611744),
                e = m(e, f, c, d, a[g + 6], 15, -1560198380),
                d = m(d, e, f, c, a[g + 13], 21, 1309151649),
                c = m(c, d, e, f, a[g + 4], 6, -145523070),
                f = m(f, c, d, e, a[g + 11], 10, -1120210379),
                e = m(e, f, c, d, a[g + 2], 15, 718787259),
                d = m(d, e, f, c, a[g + 9], 21, -343485551),
                c = n(c, h),
                d = n(d, i),
                e = n(e, o),
                f = n(f, p)
            }
            return Array(c, d, e, f)
        }
        function i(a, b, c, d, e, f) {
            return n(o(n(n(b, a), n(d, f)), e), c)
        }
        function j(a, b, c, d, e, f, g) {
            return i(b & c | ~b & d, a, b, e, f, g)
        }
        function k(a, b, c, d, e, f, g) {
            return i(b & d | c & ~d, a, b, e, f, g)
        }
        function l(a, b, c, d, e, f, g) {
            return i(b ^ c ^ d, a, b, e, f, g)
        }
        function m(a, b, c, d, e, f, g) {
            return i(c ^ (b | ~d), a, b, e, f, g)
        }
        function n(a, b) {
            var c = (65535 & a) + (65535 & b);
            return (a >> 16) + (b >> 16) + (c >> 16) << 16 | 65535 & c
        }
        function o(a, b) {
            return a << b | a >>> 32 - b
        }
        var p = 0;
        return b(a)
    }

"""


class SignError(RuntimeError):
    """execjs 与纯 Python 回退都无法完成签名时抛出。"""


class RankResult(NamedTuple):
    """榜单请求结果。

    data: 接口原始返回，类型与原实现保持一致（text 或 json）
    sign_source: 'execjs' / 'python' / 'none'（none 表示该榜单无需签名）
    """
    data: Any
    sign_source: str


def _python_nonce():
    """等价于 JS nonce()：9 位十六进制字符串。"""
    return ''.join(random.choice(_NONCE_HEX_CHARS) for _ in range(_NONCE_LENGTH))


def _python_xyz(signed_path):
    """等价于 JS b(a)：字符串按 UTF-8 编码后取 MD5，输出小写十六进制。"""
    return hashlib.md5(signed_path.encode('utf-8')).hexdigest()


def _extract_signed_text(path):
    """从 b("...") 表达式中取出被签名的字符串；不是该形式时按原样使用。

    execjs 路径是把整个 b("...") 交给 JS 求值，Python 回退需要同一段文本，
    否则两边算出的 xyz 会不一致。
    """
    expr = path.strip()
    if expr.startswith('b(') and expr.endswith(')'):
        literal = expr[2:-1].strip()
        if len(literal) >= 2 and literal[0] == literal[-1] == '"':
            try:
                return json.loads(literal)
            except ValueError:
                return literal[1:-1]
    return path


def _python_sign(path_template):
    """纯 Python 签名，返回 (nonce, xyz)；path_template 中 ***** 为 nonce 占位符。"""
    nonce = _python_nonce()
    signed_text = _extract_signed_text(path_template.replace(_NONCE_PLACEHOLDER, nonce))
    return nonce, _python_xyz(signed_text)


def _is_valid_nonce(value):
    return (
        isinstance(value, str)
        and len(value) == _NONCE_LENGTH
        and all(ch in _NONCE_HEX_CHARS for ch in value)
    )


def _is_valid_xyz(value):
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(ch in _NONCE_HEX_CHARS for ch in value)
    )


class XbRank():
    MEDIA_URL = 'https://www.newrank.cn/xdnphb/index/getMedia'
    RANK1_URL = 'https://www.newrank.cn/xdnphb/main/v1/day/rank'
    RANK2_URL = 'https://www.newrank.cn/xdnphb/nr/xinshi/video/getMainIndexRank?nonce={nonce}&xyz={xyz}'
    RANK3_URL = 'https://gw.newrank.cn/api/xd/xdnphb/nr/cloud/douyin/rank/mainHotAccountAllRankList'
    RANK4_URL = 'https://www.newrank.cn/xdnphb/nr/cloud/ks/mini/rank/accountAllRankList?nonce={nonce}&xyz={xyz}'
    RANK5_URL = 'https://www.newrank.cn/nr/bili/rank/complexMainRank?nonce={nonce}&xyz={xyz}'
    N_TOKEN = '9116298d52d64bbfb2bafa92267f74f2'

    def __init__(self, session=None):
        # 允许注入 session（测试可用假 session），默认仍走 requests
        self.session = session if session is not None else requests.session()
        self.headers = {
            'accept-language': 'zh-CN,zh;q=0.9',
            'origin': 'https://newrank.cn',
            'referer': 'https://newrank.cn/public/info/search.html?value=pythonlx&isBind=false',
            'sec-ch-ua': '" Not;A Brand";v="99", "Google Chrome";v="91", "Chromium";v="91"',
            'sec-ch-ua-mobile': '?0',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'cookie': 'UM_distinctid=17e67d04321599-0bb674ea32cd44-f791b31-1fa400-17e67d04322e09; CNZZDATA1253878005=868574852-1642412670-%7C1642412670',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'x-requested-with': 'XMLHttpRequest',
        }
        self.data = {}
        self.last_sign_source = SIGN_SOURCE_NONE
        self._js_context = None

    # ------------------------------------------------------------------
    # 签名：execjs 优先，失败回退纯 Python；回退逻辑集中在 getxyz
    # ------------------------------------------------------------------
    def _sign_by_execjs(self, path):
        """优先路径：用 execjs 执行原 JS 计算签名。"""
        if execjs is None:
            raise SignError('execjs 不可用（模块未安装或导入失败）')
        if self._js_context is None:
            self._js_context = execjs.compile(_SIGN_JS)
        nonce = self._js_context.eval('nonce()')
        signed_path = path.replace(_NONCE_PLACEHOLDER, nonce)
        xyz = self._js_context.eval(signed_path)
        if not (_is_valid_nonce(nonce) and _is_valid_xyz(xyz)):
            raise SignError(
                'execjs 返回结果不合法: nonce={!r}, xyz={!r}'.format(nonce, xyz)
            )
        return nonce, xyz

    def _sign_by_python(self, path):
        """回退路径：纯 Python 等价实现。"""
        nonce, xyz = _python_sign(path)
        if not (_is_valid_nonce(nonce) and _is_valid_xyz(xyz)):
            raise SignError(
                'Python 回退返回结果不合法: nonce={!r}, xyz={!r}'.format(nonce, xyz)
            )
        return nonce, xyz

    def getxyz(self, path):
        """集中式签名入口，五个需要签名的榜单方法共用。

        返回 (nonce, xyz, sign_source)，sign_source 为 'execjs' 或 'python'。
        execjs 抛错（含运行时缺失）时自动回退；两者都失败则抛出 SignError。
        """
        errors = []
        try:
            nonce, xyz = self._sign_by_execjs(path)
            source = SIGN_SOURCE_EXECJS
        except Exception as exc:
            errors.append('execjs: {}: {}'.format(type(exc).__name__, exc))
            logger.warning('execjs 签名失败，改用纯 Python 回退: %s', exc)
            try:
                nonce, xyz = self._sign_by_python(path)
                source = SIGN_SOURCE_PYTHON
            except Exception as py_exc:
                errors.append('python: {}: {}'.format(type(py_exc).__name__, py_exc))
                raise SignError(
                    '签名计算失败，execjs 与 Python 回退均不可用 -> ' + '; '.join(errors)
                ) from py_exc
        self.last_sign_source = source
        return nonce, xyz, source

    # ------------------------------------------------------------------
    # 请求：签名与请求只在这里执行一次
    # ------------------------------------------------------------------
    def _post(self, url, *, signed_path=None, json_body=None, form_body=None):
        """发起榜单请求。

        signed_path 非 None 时先取签名：
          - form_body 非 None：nonce/xyz 写入表单
          - 否则：url 中的 {nonce}/{xyz} 占位符被替换到 query
        返回 (response, sign_source)。
        """
        sign_source = SIGN_SOURCE_NONE
        if signed_path is not None:
            nonce, xyz, sign_source = self.getxyz(signed_path)
            if form_body is not None:
                form_body = dict(form_body, nonce=nonce, xyz=xyz)
            else:
                url = url.format(nonce=nonce, xyz=xyz)
        if form_body is not None:
            res = self.session.post(url=url, headers=self.headers, data=form_body)
        else:
            res = self.session.post(url=url, headers=self.headers, json=json_body)
        return res, sign_source

    # ------------------------------------------------------------------
    # 榜单接口
    # ------------------------------------------------------------------
    def getMedia(self, page=1):
        """ 获取新榜资讯 """
        path = 'b("/xdnphb/index/getMedia?AppKey=joker&keyword=&pageNumber={}&pageSize=10&nonce={}")'.format(
            page, _NONCE_PLACEHOLDER)
        form_body = {
            "keyword": "",
            "pageNumber": str(page),
            "pageSize": "10",
        }
        res, source = self._post(self.MEDIA_URL, signed_path=path, form_body=form_body)
        return RankResult(res.text, source)

    def getRank1(self):
        """ 微信-榜单 """
        rank_name = '百科'
        rank_name_group = '生活'
        date = '2022-01-17'
        path = 'b("/xdnphb/main/v1/day/rank?AppKey=joker&end={}&rank_name={}&rank_name_group={}&start={}&nonce={}")'.format(
            date, rank_name, rank_name_group, date, _NONCE_PLACEHOLDER)
        self.data = {
            "end": date,
            "rank_name": rank_name,
            "rank_name_group": rank_name_group,
            "start": date,
        }
        res, source = self._post(self.RANK1_URL, signed_path=path, form_body=self.data)
        return RankResult(res.json(), source)

    def getRank2(self):
        """ 视频号-榜单 """
        path = 'b("/xdnphb/data/weixinuser/getMainIndexRank?")'
        self.data = {"interval": "day",
                     "rank_date": "2021-07-11",
                     "type": "科技互联网",
                     "start": 1,
                     "size": 50}
        res, source = self._post(self.RANK2_URL, signed_path=path, json_body=self.data)
        return RankResult(res.text, source)

    def getRank3(self):
        """ 抖音号-榜单（只带 n-token，不依赖 nonce/xyz） """
        headers = dict(self.headers)
        headers['n-token'] = self.N_TOKEN
        self.data = {"size": 50, "start": 1, "type": "才艺", "date_type": "days", "date": "2021-07-10"}
        res = self.session.post(url=self.RANK3_URL, headers=headers, json=self.data)
        self.last_sign_source = SIGN_SOURCE_NONE
        return RankResult(res.text, SIGN_SOURCE_NONE)

    def getRank4(self):
        """ 快手号-榜单 """
        path = 'b("/xdnphb/nr/cloud/ks/mini/rank/accountAllRankList?")'
        self.data = {"type": "美食", "rankDate": "2021-07-10", "start": 1, "size": 100,
                     "rankType": "realTime", "sort": "newrankIndex"}
        res, source = self._post(self.RANK4_URL, signed_path=path, json_body=self.data)
        return RankResult(res.text, source)

    def getRank5(self):
        """ bilibili-榜单 """
        path = 'b("/nr/bili/rank/complexMainRank?")'
        self.data = {"numeric": "时尚", "rankDate": "2021-07-11", "start": 1, "size": 50,
                     "rankType": "0", "type": "0"}
        res, source = self._post(self.RANK5_URL, signed_path=path, json_body=self.data)
        return RankResult(res.text, source)


if __name__ == "__main__":
    xb = XbRank()
    print(xb.getRank2())
