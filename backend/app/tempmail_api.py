"""
临时邮箱 API 客户端
支持 cloudflare_temp_email 项目的 API
"""

import json
import hashlib
import re
import time
import base64
import requests
import quopri
from typing import Optional, List, Dict, Tuple
from urllib.parse import urlparse, parse_qs, unquote
# 尝试导入 logger，如果失败则使用 print
try:
    from .logger import print as log_print
except ImportError:
    def log_print(msg, _level="INFO"):
        print(f"[{_level}] {msg}")


class TempMailAPIClient:
    """临时邮箱 API 客户端"""
    
    def __init__(self, tempmail_url: str, worker_url: Optional[str] = None):
        """初始化客户端
        
        Args:
            tempmail_url: 临时邮箱 URL（包含 JWT token）
            worker_url: Worker API 地址（可选，如果不提供则从 tempmail_url 提取）
        """
        self.tempmail_url = tempmail_url
        self.jwt_token = self._extract_jwt()
        
        # 优先使用提供的 worker_url，否则从 tempmail_url 提取
        if worker_url:
            self.worker_url = worker_url.rstrip('/')
        else:
            self.worker_url = self._extract_worker_url()
        
        if not self.jwt_token:
            raise ValueError("无法从 URL 中提取 JWT token")
        
        # 记录已处理的最大邮件 ID（用于重试模式）
        self.last_max_id = 0
        # 记录最近一次观察到的邮件内容签名（mail_id -> 正文签名）。
        # 初始基线快照、每次实际提取、每轮轮询观察到的内容都会更新它：
        # - 普通/重试模式下内容未变化就不再重复提取；
        # - 重发时内容已更新（签名变化）仍会重新提取，避免“ID 相同就永远跳过”。
        self._known_signatures: Dict[int, str] = {}
        # 已确认无正文的邮件 ID（仅用于避免重复打印告警；可重试范围按单次调用跟踪）
        self._empty_content_warned = set()
        # 记录详情接口失败的邮件 ID，避免重复打印相同告警
        self._detail_failed_ids = set()
        
        # 初始化信息（简化）
        # log_print(f"[临时邮箱 API] 初始化成功\n  Worker URL: {self.worker_url}\n  JWT 长度: {len(self.jwt_token) if self.jwt_token else 0}")
    
    def _extract_jwt(self) -> Optional[str]:
        """从 URL 中提取 JWT token"""
        try:
            parsed = urlparse(self.tempmail_url)
            params = parse_qs(parsed.query)
            if 'jwt' in params:
                return params['jwt'][0]
        except Exception as e:
            log_print(f"[临时邮箱 API] 提取 JWT 失败: {e}", _level="WARNING")
        return None
    
    def _extract_worker_url(self) -> str:
        """提取 Worker 基础 URL"""
        parsed = urlparse(self.tempmail_url)
        return f"{parsed.scheme}://{parsed.netloc}"
    
    def get_email_address(self) -> Optional[str]:
        """从 JWT token 中提取邮箱地址"""
        if not self.jwt_token:
            return None
        
        try:
            parts = self.jwt_token.split('.')
            if len(parts) < 2:
                return None
            
            payload = parts[1]
            padding = '=' * (4 - len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload + padding)
            data = json.loads(decoded)
            
            if 'address' in data:
                return data['address']
        except Exception as e:
            log_print(f"[临时邮箱 API] 从 JWT 提取邮箱失败: {e}", _level="WARNING")
        
        return None
    
    def _request_mails(
        self,
        limit: int = 20,
        offset: int = 0,
        keyword: Optional[str] = None,
        address: Optional[str] = None
    ) -> Tuple[bool, List[Dict]]:
        """请求邮件列表，返回 (是否成功, 邮件列表)

        与 get_mails 的区别是能区分“请求失败”和“请求成功但没有邮件”，
        轮询时用于可靠地确定旧邮件边界。
        """
        url = f"{self.worker_url}/api/mails"
        params = {
            "limit": limit,
            "offset": offset
        }
        if keyword:
            params["keyword"] = keyword
        if address:
            params["address"] = address

        headers = {
            "Authorization": f"Bearer {self.jwt_token}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)

            # 检查是否返回 HTML（说明请求的是前端地址而不是 Worker 地址）
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" in content_type:
                if not hasattr(self, '_html_warning_logged'):
                    log_print(
                        f"[临时邮箱 API] ⚠ 检测到返回 HTML 页面，说明请求的是前端地址而不是 Worker 地址\n"
                        f"  当前 URL: {url}\n"
                        f"  请检查：\n"
                        f"  1. Worker 地址是否与前端地址不同？\n"
                        f"  2. 是否需要在系统设置中配置 'tempmail_worker_url'？\n"
                        f"  3. Worker 地址格式通常是: https://worker-name.your-subdomain.workers.dev\n"
                        f"  响应前200字符: {response.text[:200]}",
                        _level="WARNING"
                    )
                    self._html_warning_logged = True
                return False, []

            # 检查响应状态码
            if response.status_code != 200:
                # 详细错误信息（仅第一次）
                if not hasattr(self, '_error_logged'):
                    log_print(
                        f"[临时邮箱 API] 请求详情:\n"
                        f"  URL: {url}\n"
                        f"  状态码: {response.status_code}\n"
                        f"  响应头: {dict(response.headers)}\n"
                        f"  响应内容长度: {len(response.text)}\n"
                        f"  响应前500字符: {response.text[:500]}",
                        _level="WARNING"
                    )
                    self._error_logged = True
                log_print(
                    f"[临时邮箱 API] 获取邮件列表失败: HTTP {response.status_code}\n"
                    f"URL: {url}\n"
                    f"响应: {response.text[:200]}",
                    _level="WARNING"
                )
                return False, []

            # 检查响应内容类型
            if "application/json" not in content_type:
                log_print(
                    f"[临时邮箱 API] 响应不是 JSON 格式: {content_type}\n"
                    f"URL: {url}\n"
                    f"响应前200字符: {response.text[:200]}",
                    _level="WARNING"
                )
                return False, []

            # 检查响应是否为空
            if not response.text or not response.text.strip():
                log_print(
                    f"[临时邮箱 API] 响应为空\n"
                    f"URL: {url}",
                    _level="WARNING"
                )
                return False, []

            # 尝试解析 JSON
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                # 详细错误信息（仅第一次）
                if not hasattr(self, '_json_error_logged'):
                    log_print(
                        f"[临时邮箱 API] JSON 解析失败: {e}\n"
                        f"  URL: {url}\n"
                        f"  状态码: {response.status_code}\n"
                        f"  Content-Type: {response.headers.get('Content-Type', 'N/A')}\n"
                        f"  响应长度: {len(response.text)}\n"
                        f"  响应前500字符: {response.text[:500]}\n"
                        f"  完整响应: {response.text}",
                        _level="WARNING"
                    )
                    self._json_error_logged = True
                return False, []

            # 处理不同的响应格式
            if isinstance(data, list):
                return True, data
            elif isinstance(data, dict):
                for key in ("mails", "data", "result", "results"):
                    if key in data:
                        value = data[key]
                        return True, value if isinstance(value, list) else []

            log_print(
                f"[临时邮箱 API] 未知的响应格式: {type(data)}\n"
                f"响应内容: {str(data)[:200]}",
                _level="WARNING"
            )
            return False, []

        except requests.RequestException as e:
            log_print(
                f"[临时邮箱 API] 请求异常: {e}\n"
                f"URL: {url if 'url' in locals() else 'N/A'}",
                _level="WARNING"
            )
            return False, []
        except Exception as e:
            log_print(
                f"[临时邮箱 API] 未知错误: {e}\n"
                f"类型: {type(e).__name__}",
                _level="WARNING"
            )
            return False, []

    def get_mails(
        self,
        limit: int = 20,
        offset: int = 0,
        keyword: Optional[str] = None,
        address: Optional[str] = None
    ) -> List[Dict]:
        """获取邮件列表

        Args:
            limit: 返回邮件数量限制
            offset: 偏移量（分页）
            keyword: 关键词过滤（可选）
            address: 地址过滤（可选）

        Returns:
            邮件列表；请求失败时返回空列表（错误已记录日志）
        """
        _, mails = self._request_mails(
            limit=limit, offset=offset, keyword=keyword, address=address
        )
        return mails

    # 验证码邮件的关键词（顺序保持不变）
    VERIFICATION_KEYWORDS = ['gemini', 'google', 'verify', 'verification', 'code', '验证', '验证码']

    @staticmethod
    def _mail_id(mail: Dict) -> int:
        """安全地读取邮件 ID，缺失或非法时返回 0"""
        try:
            mail_id = int(mail.get("id"))
        except (TypeError, ValueError, AttributeError):
            return 0
        return mail_id if mail_id > 0 else 0

    def _dedupe_mails(self, mail_lists: List[List[Dict]]) -> List[Dict]:
        """合并多批邮件列表，按 ID 去重（同 ID 保留/补齐字段）

        四种过滤策略可能返回同一批邮件，先按 ID 去重再判断新旧，
        避免同一封邮件被多次送入提取函数。
        """
        merged: Dict[int, Dict] = {}
        invalid_id_count = 0
        for mails in mail_lists:
            if not mails:
                continue
            for mail in mails:
                if not isinstance(mail, dict):
                    continue
                mail_id = self._mail_id(mail)
                if mail_id <= 0:
                    # 没有有效 ID 的邮件无法追踪新旧，跳过
                    invalid_id_count += 1
                    continue
                existing = merged.get(mail_id)
                if existing is None:
                    merged[mail_id] = dict(mail)
                else:
                    # 同一封邮件可能来自不同过滤策略，补齐缺失字段
                    for key, value in mail.items():
                        if value and not existing.get(key):
                            existing[key] = value
        if invalid_id_count and not getattr(self, "_invalid_id_warned", False):
            sample_ids = []
            for mails in mail_lists:
                for mail in (mails or []):
                    if isinstance(mail, dict) and self._mail_id(mail) <= 0:
                        sample_ids.append(repr(mail.get("id")))
                    if len(sample_ids) >= 3:
                        break
                if len(sample_ids) >= 3:
                    break
            log_print(
                f"[临时邮箱 API] ⚠ 邮件 ID 不是有效的正整数，无法判断新旧，已跳过 "
                f"{invalid_id_count} 封（ID 示例: {', '.join(sample_ids)}）。"
                f"该轮询逻辑依赖递增的数字 ID（如 cloudflare_temp_email）",
                _level="WARNING"
            )
            self._invalid_id_warned = True
        result = list(merged.values())
        result.sort(key=self._mail_id, reverse=True)
        return result

    def _collect_mails(
        self,
        target_email: Optional[str],
        limit: int,
        threshold: int
    ) -> Tuple[List[Dict], List[str]]:
        """按四种过滤策略收集邮件，合并后按 ID 去重

        策略顺序：关键词+地址 → 仅地址 → 仅关键词 → 无过滤。
        如果某一步已经拿到 ID 大于 threshold 的新邮件，就不再请求后续策略；
        否则继续尝试其他策略，避免关键词过滤只命中旧邮件而漏掉新邮件。

        Returns:
            (去重并按 ID 降序排列的邮件列表, 实际使用的策略描述列表)
        """
        collected: List[List[Dict]] = []
        used: List[str] = []
        fresh_found = False

        def add(mails: List[Dict], description: str) -> bool:
            if not mails:
                return False
            collected.append(mails)
            used.append(description)
            return any(self._mail_id(mail) > threshold for mail in mails)

        # 策略1：关键词 + 地址过滤（精确匹配）
        if target_email:
            for keyword in self.VERIFICATION_KEYWORDS:
                mails = self.get_mails(limit=limit, keyword=keyword, address=target_email)
                if mails:
                    fresh_found = add(
                        mails,
                        f"关键词+地址过滤 (keyword='{keyword}', address='{target_email}')"
                    )
                    break

            # 策略2：仅地址过滤（不使用关键词）
            if not fresh_found:
                mails = self.get_mails(limit=limit, address=target_email)
                fresh_found = add(mails, f"地址过滤 (address='{target_email}')")

        # 策略3：仅关键词过滤（不使用地址）
        if not fresh_found:
            for keyword in self.VERIFICATION_KEYWORDS:
                mails = self.get_mails(limit=limit, keyword=keyword)
                if mails:
                    fresh_found = add(mails, f"关键词过滤 (keyword='{keyword}')")
                    break

        # 策略4：无过滤（获取所有邮件）
        if not fresh_found:
            mails = self.get_mails(limit=limit)
            add(mails, "无过滤（获取所有邮件）")

        return self._dedupe_mails(collected), used

    def _select_candidates(
        self,
        mails: List[Dict],
        threshold: int,
        resend_requested: bool,
        resend_floor: int,
        retryable_ids: Optional[set] = None,
        only_latest: bool = False
    ) -> List[Dict]:
        """挑选本次需要检查的邮件（按 ID 降序）

        - 普通模式：只处理 ID > threshold 的新邮件；
        - retryable_ids：本次调用中“无正文 / 未提取到验证码”的邮件，正文或内容
          可能随后补全，允许在本次调用内再次检查（内容未变化时由签名对比跳过）；
        - 重发模式：额外允许重新检查 ID == resend_floor 的邮件，用于捕获
          “邮件 ID 未增长但内容已更新”的重发邮件（内容是否变化在提取前再判断）；
        - only_latest=True（重试模式且没有已处理记录）：只检查最新一封，
          保留重试语义的同时不会把历史邮件的旧验证码当成本次结果。
        """
        retryable_ids = retryable_ids or set()
        candidates = []
        for mail in mails:
            mail_id = self._mail_id(mail)
            if mail_id <= 0:
                continue
            if mail_id > threshold:
                candidates.append(mail)
            elif mail_id in retryable_ids:
                # 本次调用中尚未拿到有效内容，允许再次检查
                candidates.append(mail)
            elif resend_requested and resend_floor > 0 and mail_id == resend_floor:
                # 重发：允许重新检查“ID 未增长”的最新邮件。
                # 内容是否真的更新由调用循环里的签名对比决定：
                # 内容没变就不重复提取，内容变了（或基线时无正文、现在有正文）
                # 就重新提取，避免“ID 相同就永远跳过”。
                candidates.append(mail)
        candidates.sort(key=self._mail_id, reverse=True)
        if only_latest:
            candidates = candidates[:1]
        return candidates

    @staticmethod
    def _mail_signature(mail_text: str) -> str:
        """计算邮件正文签名，用于判断同一 ID 的邮件内容是否更新"""
        if not mail_text:
            return ""
        return hashlib.sha1(mail_text.encode("utf-8", "ignore")).hexdigest()

    def _advance_last_max_id(self, mail_id: int):
        """记录已处理的最大邮件 ID"""
        if mail_id > self.last_max_id:
            self.last_max_id = mail_id

    def _record_processed(self, mail_id: int, signature: str):
        """记录某封邮件最近一次观察到的内容签名"""
        self._known_signatures[mail_id] = signature
        max_entries = 500
        if len(self._known_signatures) > max_entries:
            # dict 保持插入顺序，丢弃最早记录的条目
            for stale_id in list(self._known_signatures)[: len(self._known_signatures) - max_entries]:
                self._known_signatures.pop(stale_id, None)

    def _remember_baseline_mails(self, mails: List[Dict], detail_id: int = 0):
        """记录基线邮件的内容签名（此时它们都视为旧邮件）

        只用列表字段计算签名，避免为所有旧邮件调用详情接口；
        但对 detail_id（重发时可能被重新检查的最大 ID 邮件）额外获取一次正文，
        确保“内容是否更新”的判断有可靠依据。
        """
        for mail in mails:
            mail_id = self._mail_id(mail)
            if mail_id <= 0:
                continue
            if mail_id == detail_id:
                text = self._get_mail_text(mail)
            else:
                text = self._get_mail_text(mail, allow_detail=False)
            self._known_signatures[mail_id] = self._mail_signature(text)

    @staticmethod
    def _strip_html(html_content: str) -> str:
        """移除 HTML 标签并压缩空白"""
        if not html_content:
            return ""
        text = re.sub(r'<[^>]+>', '', html_content)
        return re.sub(r'\s+', ' ', text).strip()

    def _parse_raw_mail(self, raw_content: str) -> str:
        """从邮件原始内容（含邮件头部）中解析正文"""
        if not raw_content:
            return ""

        # 方法1：查找 HTML 正文部分（最可靠）
        html_match = re.search(r'<html[^>]*>.*?</html>', raw_content, re.DOTALL | re.IGNORECASE)
        if html_match:
            return self._strip_html(html_match.group(0))

        # 方法2：查找邮件正文的开始位置（邮件头部和正文之间通常有一个空行）
        body_start = raw_content.find('\r\n\r\n')
        if body_start == -1:
            body_start = raw_content.find('\n\n')

        if body_start > 0:
            raw_content = raw_content[body_start:].strip()
            html_match = re.search(r'<html[^>]*>.*?</html>', raw_content, re.DOTALL | re.IGNORECASE)
            if html_match:
                raw_content = html_match.group(0)
            return self._strip_html(raw_content)

        # 方法3：如果找不到空行分隔，移除所有邮件头部行
        body_lines = []
        in_body = False
        for line in raw_content.split('\n'):
            if not line.strip():
                in_body = True
                continue
            if 'Content-Type:' in line and 'text/' in line:
                in_body = True
                continue
            if not in_body and (line.startswith('Received:') or
                                line.startswith('ARC-') or
                                line.startswith('Return-Path:') or
                                line.startswith('Delivered-To:') or
                                line.startswith('X-') or
                                line.startswith('MIME-Version:') or
                                line.startswith('Content-Type:') or
                                line.startswith('Content-Transfer-Encoding:') or
                                line.startswith('Date:') or
                                line.startswith('From:') or
                                line.startswith('To:') or
                                line.startswith('Subject:') or
                                line.startswith('Message-ID:')):
                continue
            body_lines.append(line)

        return self._strip_html('\n'.join(body_lines))

    def _normalize_mail_text(self, mail_text: str) -> str:
        """解码 Quoted-Printable / URL 编码并规范化文本"""
        if not mail_text:
            return ""

        # 解码 Quoted-Printable 编码（如果存在）
        if '=' in mail_text:
            try:
                # 先移除 Quoted-Printable 的换行标记（`=\r\n`, `=\n`, `=\r`）
                mail_text_cleaned = mail_text.replace('=\r\n', '').replace('=\n', '').replace('=\r', '')
                # 如果包含 Quoted-Printable 编码模式（如 `=E9=AA=8C`），尝试解码
                if re.search(r'=[0-9A-F]{2}', mail_text_cleaned):
                    mail_text = quopri.decodestring(
                        mail_text_cleaned.encode('latin-1')
                    ).decode('utf-8', errors='ignore')
                else:
                    # 即使不是标准 Quoted-Printable，也移除 `=` 符号（可能是解码后的残留）
                    mail_text = mail_text_cleaned
            except Exception as e:
                log_print(f"[临时邮箱 API] ⚠ Quoted-Printable 解码失败: {e}", _level="WARNING")
                # 解码失败时，至少移除 `=` 符号
                mail_text = mail_text.replace('=', ' ')

        # 再次移除所有残留的 `=` 符号（确保完全清理）
        if mail_text and '=' in mail_text:
            mail_text = re.sub(r'=\s*', '', mail_text)

        # 解码 URL 编码（如果存在）
        if mail_text and '%' in mail_text:
            try:
                mail_text = unquote(mail_text)
            except Exception as e:
                log_print(f"[临时邮箱 API] ⚠ URL 解码失败: {e}", _level="WARNING")

            # 规范化文本：合并多个空格，处理换行，移除残留的 `=` 符号
            # 注意：在移除 `=` 符号时，要保护关键短语，避免“验证码”被截断
            if mail_text:
                protected_phrases = {
                    'VERIFICATION_CODE_PLACEHOLDER_1': '一次性验证码为',
                    'VERIFICATION_CODE_PLACEHOLDER_2': '验证码为',
                    'VERIFICATION_CODE_PLACEHOLDER_3': '验证码是',
                    'VERIFICATION_CODE_PLACEHOLDER_4': 'verification code is',
                    'VERIFICATION_CODE_PLACEHOLDER_5': 'one-time verification code is',
                }

                # 保护关键短语
                for placeholder, phrase in protected_phrases.items():
                    if phrase in mail_text:
                        mail_text = mail_text.replace(phrase, placeholder)

                # 移除所有单独的 `=` 符号（Quoted-Printable 的换行标记残留）
                mail_text = re.sub(r'=\s+', '', mail_text)  # `= ` 或 `=\n` 等直接移除
                mail_text = re.sub(r'\s+=\s+', ' ', mail_text)  # ` = ` 替换为空格
                mail_text = re.sub(r'=\s*$', '', mail_text, flags=re.MULTILINE)  # 行尾的 `=`
                mail_text = re.sub(r'^\s*=\s*', '', mail_text, flags=re.MULTILINE)  # 行首的 `=`

                # 恢复关键短语
                for placeholder, phrase in protected_phrases.items():
                    mail_text = mail_text.replace(placeholder, phrase)

                # 合并多个空格（但保留换行，因为验证码可能在单独一行）
                mail_text = re.sub(r'[ \t]+', ' ', mail_text)
                mail_text = re.sub(r'\n{3,}', '\n\n', mail_text)
                mail_text = mail_text.strip()

        return mail_text

    def _get_mail_text(self, mail: Dict, allow_detail: bool = True) -> str:
        """获取邮件正文

        优先级保持为：text → 详情接口 → raw → html/content/body。
        详情接口失败或返回空正文时继续尝试后续来源；全部为空时返回空字符串。

        Args:
            allow_detail: 是否允许调用详情接口（建立基线时关闭，避免多余请求）
        """
        mail_id = self._mail_id(mail)
        mail_text = mail.get("text", "") or ""

        # 列表接口没有 text 时，优先调用详情接口
        if not mail_text and allow_detail and mail_id > 0:
            detail_data = self._get_mail_detail(mail_id)
            if detail_data:
                mail_text = detail_data.get("text", "") or ""
                if not mail_text:
                    raw_content = detail_data.get("raw", "") or ""
                    if raw_content:
                        mail_text = self._parse_raw_mail(raw_content)
                if not mail_text:
                    html_content = (
                        detail_data.get("html", "") or
                        detail_data.get("content", "") or
                        detail_data.get("body", "") or ""
                    )
                    if html_content:
                        mail_text = self._strip_html(html_content)

        # 仍然没有内容，尝试列表邮件自带的 raw 字段
        if not mail_text:
            raw_content = mail.get("raw", "") or ""
            if raw_content:
                mail_text = self._parse_raw_mail(raw_content)

        # 最后尝试列表邮件自带的 html/content/body 字段
        if not mail_text:
            html_content = (
                mail.get("html", "") or
                mail.get("content", "") or
                mail.get("body", "") or ""
            )
            if html_content:
                mail_text = self._strip_html(html_content)

        return self._normalize_mail_text(mail_text)

    def _get_mail_detail(self, mail_id: int) -> Optional[Dict]:
        """调用详情接口获取单封邮件；失败时返回 None（不抛出异常）"""
        try:
            detail_url = f"{self.worker_url}/api/mails/{mail_id}"
            headers = {
                "Authorization": f"Bearer {self.jwt_token}",
                "Content-Type": "application/json"
            }
            detail_response = requests.get(detail_url, headers=headers, timeout=30)
            if detail_response.status_code != 200:
                if mail_id not in self._detail_failed_ids:
                    log_print(
                        f"[临时邮箱 API] ⚠ 获取邮件详情失败 (ID {mail_id}): "
                        f"HTTP {detail_response.status_code}",
                        _level="WARNING"
                    )
                    self._detail_failed_ids.add(mail_id)
                return None
            detail_data = detail_response.json()
            return detail_data if isinstance(detail_data, dict) else None
        except Exception as e:
            if mail_id not in self._detail_failed_ids:
                log_print(f"[临时邮箱 API] ⚠ 获取邮件详情失败 (ID {mail_id}): {e}", _level="WARNING")
                self._detail_failed_ids.add(mail_id)
            return None

    def get_verification_code(
        self,
        timeout: int = 120,
        retry_mode: bool = False,
        extract_code_func=None,
        resend_requested: bool = False
    ) -> Optional[str]:
        """获取验证码

        轮询规则：
        - 首次调用（retry_mode=False）：先记录当前最大邮件 ID，只处理 ID 更大的新邮件；
        - 重试模式（retry_mode=True）：沿用实例上已处理的最大邮件 ID，只检查一次，
          且不重复提取已检查过的同一封邮件；
        - 重发（resend_requested=True）：调用方明确表示用户点击了“重新发送验证码”，
          允许重新检查 ID 未增长但内容已更新的邮件。

        Args:
            timeout: 超时时间（秒）
            retry_mode: 是否为重试模式
            extract_code_func: 验证码提取函数（从 auto_login_with_email 导入）
            resend_requested: 调用方是否明确表示用户点击了“重新发送验证码”

        Returns:
            验证码字符串，如果未找到则返回 None
        """
        # 获取邮箱地址（不显示）
        email_address = self.get_email_address()
        if not email_address:
            log_print("[临时邮箱 API] ⚠ 无法从 JWT 中提取邮箱地址", _level="WARNING")

        if not extract_code_func:
            # 如果没有提供提取函数，尝试导入
            try:
                import sys
                from pathlib import Path
                project_root = Path(__file__).parent.parent
                if str(project_root) not in sys.path:
                    sys.path.insert(0, str(project_root))
                from auto_login_with_email import extract_verification_code
                extract_code_func = extract_verification_code
            except ImportError:
                log_print("[临时邮箱 API] ✗ 无法导入验证码提取函数", _level="ERROR")
                return None

        target_email = email_address

        start_time = time.time()
        poll_interval = 2 if retry_mode else 5
        max_attempts = max(1, timeout // poll_interval)

        # 确定“旧邮件”阈值
        baseline_known = True
        if retry_mode:
            baseline_max_id = self.last_max_id
            if baseline_max_id > 0:
                log_print(
                    f"[临时邮箱 API] 重试模式：沿用已处理的最大邮件 ID {baseline_max_id}，"
                    f"只处理 ID 更大的新邮件",
                    _level="INFO"
                )
            else:
                # 没有任何已处理记录：只检查最新一封，保留“立即获取”的重试语义，
                # 同时不会把历史邮件的旧验证码当成结果
                log_print(
                    "[临时邮箱 API] 重试模式：没有已处理的邮件 ID，仅检查最新一封邮件",
                    _level="INFO"
                )
        else:
            baseline_max_id = self.last_max_id
            snapshot_ok = False
            snapshot_mails: List[Dict] = []
            # 同时尝试“无过滤”和“地址过滤”两种快照并合并：
            # 地址过滤参数可能不被 Worker 支持而返回空列表，
            # 只用地址过滤会把历史邮件误判为新邮件。
            attempts_left = 3
            while attempts_left > 0 and not snapshot_ok:
                attempts_left -= 1
                collected = []
                ok_any = False
                for query in ({"limit": 50}, {"limit": 50, "address": target_email}):
                    if query.get("address") is None and "address" in query:
                        continue
                    ok, mails = self._request_mails(**query)
                    if ok:
                        ok_any = True
                        collected.append(mails)
                if ok_any:
                    snapshot_ok = True
                    snapshot_mails = self._dedupe_mails(collected)
                else:
                    time.sleep(2)

            if snapshot_ok:
                if snapshot_mails:
                    snapshot_max_id = max(self._mail_id(mail) for mail in snapshot_mails)
                    baseline_max_id = max(snapshot_max_id, self.last_max_id)
                # 记录基线邮件的内容，供重发时判断“内容是否已更新”
                self._remember_baseline_mails(snapshot_mails, detail_id=baseline_max_id)
                self.last_max_id = max(self.last_max_id, baseline_max_id)
                log_print(
                    f"[临时邮箱 API] 记录当前最大邮件 ID: {baseline_max_id}，"
                    f"将只处理新邮件（ID > {baseline_max_id}）",
                    _level="INFO"
                )
            else:
                if baseline_max_id > 0:
                    # 本次快照失败，但实例上已有上次处理记录的 ID：
                    # 以它为边界仍然能保证不返回旧邮件
                    baseline_known = True
                    log_print(
                        f"[临时邮箱 API] ⚠ 初始邮件列表请求失败，沿用上次已处理的最大邮件 ID "
                        f"{baseline_max_id} 作为旧邮件边界",
                        _level="WARNING"
                    )
                else:
                    # 无法取得初始快照且没有任何已处理记录：本次暂时无法区分新旧邮件。
                    # 在轮询中继续尝试获取基线；若始终无法确定，则不返回任何验证码，
                    # 绝不把历史邮件的旧验证码当成新结果。
                    baseline_known = False
                    log_print(
                        "[临时邮箱 API] ⚠ 初始邮件列表请求失败，暂时无法确定旧邮件边界，"
                        "将在轮询中重试获取基线",
                        _level="WARNING"
                    )

            # 等待 10 秒，确保验证码邮件已发送并到达
            log_print("[临时邮箱 API] 等待验证码邮件到达（10秒）...", _level="INFO")
            time.sleep(10)

        # 重发场景：允许重新检查 ID == resend_floor 的邮件
        resend_floor = max(baseline_max_id, self.last_max_id) if resend_requested else 0
        if resend_requested:
            log_print(
                f"[临时邮箱 API] 重发模式：允许重新检查 ID == {resend_floor} 的最新邮件"
                f"（内容更新时重新提取）",
                _level="INFO"
            )

        attempts = 0
        checked_ids = set()       # 真正送入提取函数的邮件 ID
        fetched_ids = set()       # 轮询中见过的所有邮件 ID
        no_content_ids = set()    # 本次调用中已检查但正文为空的邮件 ID
        # 本次调用中“内容尚不可用/未提取到验证码”的邮件，允许后续轮询重新检查；
        # 明确限定在本次调用内，重试模式不会因此重新处理上一轮已失败的邮件
        retryable_ids = set()
        timed_out = False
        attempted_signatures: Dict[int, set] = {}  # 本次调用中每封邮件已尝试的内容签名
        logged_candidate_ids = set()               # 已打印过“发现待处理邮件”的 ID

        while attempts < max_attempts:
            attempts += 1
            elapsed = int(time.time() - start_time)

            if elapsed >= timeout:
                timed_out = True
                break

            # 首次调用未能取得初始快照时，在轮询中补做一次：
            # 以首个成功响应的最大 ID 作为旧邮件边界，绝不把边界之前的邮件当成新邮件。
            if not baseline_known:
                queries = [{"limit": 50}]
                if target_email:
                    queries.append({"limit": 50, "address": target_email})
                collected = []
                ok_any = False
                for query in queries:
                    ok, snapshot_mails = self._request_mails(**query)
                    if ok:
                        ok_any = True
                        collected.append(snapshot_mails)
                if not ok_any:
                    time.sleep(poll_interval)
                    continue
                snapshot_mails = self._dedupe_mails(collected)
                snapshot_max_id = max(
                    (self._mail_id(mail) for mail in snapshot_mails), default=0
                )
                baseline_max_id = max(snapshot_max_id, baseline_max_id, self.last_max_id)
                self._remember_baseline_mails(snapshot_mails, detail_id=baseline_max_id)
                self.last_max_id = baseline_max_id
                baseline_known = True
                if resend_requested:
                    resend_floor = baseline_max_id
                log_print(
                    f"[临时邮箱 API] 补记当前最大邮件 ID: {baseline_max_id}，"
                    f"该 ID 及更早的邮件视为旧邮件",
                    _level="INFO"
                )
                time.sleep(poll_interval)
                continue

            threshold = max(baseline_max_id, self.last_max_id)
            mail_limit = 50 if retry_mode else 20

            # 收集邮件：四种过滤策略的结果合并后按 ID 去重
            mails, strategies_used = self._collect_mails(target_email, mail_limit, threshold)

            if mails:
                fetched_ids.update(
                    self._mail_id(mail) for mail in mails if self._mail_id(mail) > 0
                )

            candidates = self._select_candidates(
                mails, threshold, resend_requested, resend_floor,
                retryable_ids=retryable_ids,
                only_latest=(retry_mode and baseline_max_id == 0)
            )
            candidates_needing_log = [
                mail for mail in candidates
                if self._mail_id(mail) not in logged_candidate_ids
            ]

            if candidates and candidates_needing_log:
                newest_id = max(self._mail_id(mail) for mail in candidates_needing_log)
                log_print(
                    f"[临时邮箱 API] ✓ 发现待处理邮件 {len(candidates)} 封"
                    f"（最新 ID: {newest_id}，策略: {'; '.join(strategies_used) or '未知'}）",
                    _level="INFO"
                )
                logged_candidate_ids.update(
                    self._mail_id(mail) for mail in candidates_needing_log
                )

            for mail in candidates:
                mail_id = self._mail_id(mail)
                if mail_id <= 0:
                    continue

                mail_text = self._get_mail_text(mail)

                # 先处理“无正文”：标记为本次调用可重试，
                # 这样正文稍后到达时仍能在后续轮询中被取到并提取
                if not mail_text:
                    no_content_ids.add(mail_id)
                    retryable_ids.add(mail_id)
                    if mail_id not in self._empty_content_warned:
                        self._empty_content_warned.add(mail_id)
                        log_print(
                            f"[临时邮箱 API] ⚠ 邮件 ID {mail_id} 无正文内容"
                            f"（text/详情接口/raw/html 均为空），继续等待后续邮件",
                            _level="WARNING"
                        )
                    continue

                no_content_ids.discard(mail_id)

                signature = self._mail_signature(mail_text)

                seen_signatures = attempted_signatures.setdefault(mail_id, set())
                if signature in seen_signatures:
                    # 本次调用中已经检查过相同内容的这封邮件
                    continue

                # 内容与此前观察到的一致：不再重复提取。
                # 重发时若内容已更新（签名变化），则会继续往下执行提取。
                recorded_signature = self._known_signatures.get(mail_id)
                if recorded_signature and recorded_signature == signature:
                    seen_signatures.add(signature)
                    continue

                seen_signatures.add(signature)

                # 拿到正文后不再是“无正文”邮件，并记录本次内容
                self._empty_content_warned.discard(mail_id)
                self._record_processed(mail_id, signature)
                if mail_id > self.last_max_id:
                    self._advance_last_max_id(mail_id)
                checked_ids.add(mail_id)
                try:
                    code = extract_code_func(mail_text)
                except Exception as e:
                    log_print(
                        f"[临时邮箱 API] ⚠ 邮件 ID {mail_id} 验证码提取异常: {e}",
                        _level="WARNING"
                    )
                    code = None

                if code:
                    retryable_ids.discard(mail_id)
                    actual_wait_time = int(time.time() - start_time)
                    log_print(
                        f"[临时邮箱 API] ✓ 从邮件 ID {mail_id} 中提取到验证码: {code} "
                        f"(等待时间: {actual_wait_time} 秒)"
                    )
                    return code

                # 内容未含有效验证码。只有重发场景才需要在后续轮询中重新检查
                # （用户可能重发后同一封邮件内容更新）；否则不重复送入提取函数
                if resend_requested:
                    retryable_ids.add(mail_id)
                else:
                    retryable_ids.discard(mail_id)
                log_print(
                    f"[临时邮箱 API] 邮件 ID {mail_id} 未包含有效验证码"
                    f"（内容未变化时不会重复提取）",
                    _level="INFO"
                )

            # 重试模式（非重发）只检查一次
            if retry_mode and not resend_requested:
                break

            if not mails:
                if attempts % 4 == 0:  # 每 4 次尝试打印一次日志
                    log_print(f"[临时邮箱 API] 等待邮件到达... (已等待 {elapsed} 秒)")
            elif not candidates:
                if attempts % 2 == 0:  # 每 2 次尝试打印一次日志
                    log_print(
                        f"[临时邮箱 API] 等待新邮件（阈值 ID > {threshold}，"
                        f"已等待 {elapsed} 秒）...",
                        _level="INFO"
                    )

            time.sleep(poll_interval)

        # 未找到验证码：输出可判断的失败摘要
        elapsed = int(time.time() - start_time)
        self.last_max_id = max(self.last_max_id, baseline_max_id)
        threshold = max(baseline_max_id, self.last_max_id)

        if timed_out:
            log_print(
                f"[临时邮箱 API] ✗ 超时（{timeout} 秒）未获取到验证码"
                f"（尝试 {attempts} 次，耗时 {elapsed} 秒）",
                _level="WARNING"
            )
        else:
            log_print(
                f"[临时邮箱 API] ✗ 未找到验证码（尝试 {attempts} 次，耗时 {elapsed} 秒）",
                _level="WARNING"
            )

        if fetched_ids:
            fetched_min, fetched_max = min(fetched_ids), max(fetched_ids)
            if checked_ids:
                checked_min, checked_max = min(checked_ids), max(checked_ids)
                log_print(
                    f"[临时邮箱 API] 已获取 {len(fetched_ids)} 封邮件"
                    f"（ID 范围: {fetched_min}-{fetched_max}），"
                    f"已检查 {len(checked_ids)} 封"
                    f"（已检查邮件 ID 范围: {checked_min}-{checked_max}，"
                    f"其中 {len(no_content_ids)} 封无正文），均未包含有效验证码"
                    f"（本次新邮件阈值: ID > {threshold}）",
                    _level="WARNING"
                )
            else:
                log_print(
                    f"[临时邮箱 API] 已获取 {len(fetched_ids)} 封邮件"
                    f"（ID 范围: {fetched_min}-{fetched_max}），"
                    f"其中 {len(no_content_ids)} 封无正文；"
                    f"没有待检查的新邮件（本次新邮件阈值: ID > {threshold}），"
                    f"未执行验证码提取",
                    _level="WARNING"
                )
        else:
            log_print(
                f"[临时邮箱 API] 未获取到任何邮件（等待的新邮件阈值: ID > {threshold}）",
                _level="WARNING"
            )
        return None


def get_verification_code_from_api(
    tempmail_url: str,
    timeout: int = 120,
    retry_mode: bool = False,
    extract_code_func=None,
    worker_url: Optional[str] = None,
    resend_requested: bool = False
) -> Optional[str]:
    """通过 API 获取验证码（便捷函数）

    Args:
        tempmail_url: 临时邮箱 URL（包含 JWT token）
        timeout: 超时时间（秒）
        retry_mode: 是否为重试模式
        extract_code_func: 验证码提取函数
        worker_url: Worker API 地址（可选，如果不提供则从 tempmail_url 提取）
        resend_requested: 调用方是否明确表示用户点击了“重新发送验证码”

    Returns:
        验证码字符串，如果未找到则返回 None
    """
    try:
        client = TempMailAPIClient(tempmail_url, worker_url)
        return client.get_verification_code(
            timeout, retry_mode, extract_code_func, resend_requested
        )
    except Exception as e:
        log_print(f"[临时邮箱 API] 初始化客户端失败: {e}", _level="ERROR")
        return None

