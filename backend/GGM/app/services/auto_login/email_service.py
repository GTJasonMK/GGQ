"""
邮件验证码服务

通过 IMAP 协议从 QQ 邮箱获取 Google 登录验证码
支持异步非阻塞操作，不会阻塞主事件循环
"""
import re
import time
import asyncio
import imaplib
import email
import email.message
from email.header import decode_header
from datetime import datetime, timedelta
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class EmailVerificationService:
    """
    QQ 邮箱验证码服务

    通过 IMAP 协议从 QQ 邮箱获取 Google 登录验证码
    所有网络操作都通过 asyncio.to_thread 异步执行，不会阻塞事件循环
    """

    # Google 验证码邮件特征
    GOOGLE_SENDERS = [
        "noreply-googlecloud@google.com",
        "noreply@google.com",
        "no-reply@accounts.google.com",
    ]

    # 验证码正则模式（按优先级排序，越精确的放前面）
    VERIFICATION_CODE_PATTERNS = [
        # Google Business 验证码格式：验证码在单独一行
        r'一次性验\s*证码为[：:]\s*\n+\s*([A-Z0-9]{6})',
        r'验证码为[：:]\s*\n+\s*([A-Z0-9]{6})',
        # 旧格式（验证码在同一行）
        r'一次性验证码[\s\n]+为[：:][\s\n]*([A-Z0-9]{6})',
        r'验证码[\s\n]+为[：:][\s\n]*([A-Z0-9]{6})',
        r'验证[码\s\n]*为[：:\s]*\n*\s*([A-Z0-9]{6})',
        r'code[：:\s]+([A-Z0-9]{6})',
        r'G-(\d{6})',
        # 通用格式
        r'验证码[：:]\s*([A-Z0-9]{6})',
        r'verification code[：:\s]*([A-Z0-9]{6})',
        r'security code[：:\s]*([A-Z0-9]{6})',
        # 更宽松的模式：独立行的6位代码
        r'\n\s*([A-Z0-9]{6})\s*\n',
        # 尝试匹配任何被空白包围的6位验证码（带前后文验证）
        r'(?:验证码|code|Code)[^\d]*(\d{6})',
    ]

    def __init__(self, config: dict):
        """
        初始化邮件服务

        Args:
            config: 配置字典，包含:
                - address: QQ 邮箱地址
                - auth_code: IMAP 授权码
                - imap_server: IMAP 服务器（默认 imap.qq.com）
                - imap_port: IMAP 端口（默认 993）
        """
        self.email_address = config.get("address", "")
        self.auth_code = config.get("auth_code", "")
        self.imap_server = config.get("imap_server", "imap.qq.com")
        self.imap_port = config.get("imap_port", 993)
        self._connection: Optional[imaplib.IMAP4_SSL] = None
        self._search_email_count = 5
        self._lock = asyncio.Lock()  # 保护 IMAP 连接的并发访问

    def _connect_sync(self) -> bool:
        """同步连接到 IMAP 服务器（内部方法）"""
        try:
            self._connection = imaplib.IMAP4_SSL(
                host=self.imap_server,
                port=self.imap_port
            )
            self._connection.login(self.email_address, self.auth_code)
            self._connection.select("INBOX")
            print(f"  [邮箱] 已连接到 {self.imap_server}")
            logger.info("邮箱连接成功")
            return True
        except Exception as e:
            print(f"  [邮箱] 连接失败: {e}")
            logger.error(f"连接邮箱失败: {e}")
            return False

    async def connect(self) -> bool:
        """异步连接到 IMAP 服务器"""
        return await asyncio.to_thread(self._connect_sync)

    def _disconnect_sync(self):
        """同步断开连接（内部方法）"""
        if self._connection:
            try:
                self._connection.logout()
            except:
                pass
            self._connection = None

    async def disconnect(self):
        """异步断开连接"""
        await asyncio.to_thread(self._disconnect_sync)

    def _refresh_inbox_sync(self) -> bool:
        """同步刷新收件箱状态（内部方法）"""
        try:
            self._connection.noop()
            self._connection.select("INBOX")
            return True
        except:
            return False

    async def fetch_verification_code(
        self,
        timeout: int = 120,
        poll_interval: int = 2,
        since_time: Optional[datetime] = None,
        target_email: Optional[str] = None
    ) -> Optional[str]:
        """
        异步获取 Google 验证码

        使用 asyncio.to_thread 执行 IMAP 操作，不会阻塞事件循环

        Args:
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔（秒）
            since_time: 只查找此时间之后的邮件
            target_email: 目标 Google 邮箱（用于精确匹配）

        Returns:
            6位验证码字符串，失败返回 None
        """
        if since_time is None:
            since_time = datetime.now() - timedelta(minutes=2)

        async with self._lock:  # 保护 IMAP 连接的并发访问
            if not self._connection:
                if not await self.connect():
                    return None

            if target_email:
                print(f"  [邮箱] 正在获取 {target_email} 的验证码...")
                print(f"  [邮箱] 请求时间: {since_time.strftime('%H:%M:%S')}")
            else:
                print(f"  [邮箱] 正在获取验证码...")

            start_time = time.time()
            poll_count = 0

            while time.time() - start_time < timeout:
                poll_count += 1

                # 使用 to_thread 执行同步的 IMAP 刷新操作
                refresh_ok = await asyncio.to_thread(self._refresh_inbox_sync)
                if not refresh_ok:
                    if not await self.connect():
                        print(f"  [邮箱] IMAP连接失败，重试中...")
                        await asyncio.sleep(1)  # 使用异步 sleep
                        continue

                # 使用 to_thread 执行同步的邮件搜索操作
                code, msg_id = await asyncio.to_thread(
                    self._search_verification_email_sync,
                    target_email,
                    since_time
                )
                if code:
                    # 删除邮件也异步执行
                    await asyncio.to_thread(self._delete_email_sync, msg_id)
                    return code

                elapsed = int(time.time() - start_time)
                print(f"  [邮箱] 等待验证码... ({elapsed}秒/{timeout}秒) [轮询#{poll_count}]")
                await asyncio.sleep(poll_interval)  # 使用异步 sleep，不阻塞事件循环

            print(f"  [邮箱] 获取验证码超时 ({timeout}秒)", flush=True)
            return None

    def _search_verification_email_sync(
        self,
        target_email: Optional[str] = None,
        since_time: Optional[datetime] = None
    ) -> Tuple[Optional[str], Optional[bytes]]:
        """同步搜索验证码邮件（内部方法）"""
        try:
            typ, msg_nums = self._connection.search(
                None,
                'FROM', '"noreply-googlecloud@google.com"'
            )

            if typ != "OK" or not msg_nums[0]:
                print(f"    [搜索] 未找到 Google 验证码邮件", flush=True)
                return None, None

            msg_ids = msg_nums[0].split()
            recent_ids = msg_ids[-self._search_email_count:] if len(msg_ids) > self._search_email_count else msg_ids
            recent_ids.reverse()

            print(f"    [搜索] 找到 {len(msg_ids)} 封 Google 邮件，检查最新 {len(recent_ids)} 封...", flush=True)

            for msg_id in recent_ids:
                code = self._extract_code_sync(msg_id, target_email, since_time)
                if code:
                    return code, msg_id

            return None, None

        except Exception as e:
            print(f"    [搜索] 搜索邮件出错: {e}", flush=True)
            logger.debug(f"搜索邮件出错: {e}")
            return None, None

    def _extract_code_sync(
        self,
        msg_id: bytes,
        target_email: Optional[str] = None,
        since_time: Optional[datetime] = None
    ) -> Optional[str]:
        """同步从邮件中提取验证码（内部方法）"""
        try:
            typ, msg_data = self._connection.fetch(msg_id, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                return None

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            # 检查发件人
            from_addr = msg.get("From", "").lower()
            if "google" not in from_addr:
                return None

            # 检查主题
            subject = self._decode_header_value(msg.get("Subject", ""))
            if "验证码" not in subject and "verification" not in subject.lower() and "code" not in subject.lower():
                return None

            # 获取邮件基本信息用于日志
            to_addr = self._decode_header_value(msg.get("To", ""))
            date_str = msg.get("Date", "")
            mail_time_str = "未知"
            mail_time_local = None

            # 解析邮件时间
            if date_str:
                try:
                    from email.utils import parsedate_to_datetime
                    mail_time = parsedate_to_datetime(date_str)
                    if mail_time.tzinfo:
                        mail_time_local = mail_time.astimezone().replace(tzinfo=None)
                    else:
                        mail_time_local = mail_time
                    mail_time_str = mail_time_local.strftime("%H:%M:%S")
                except Exception:
                    pass

            # 打印检测到的邮件信息
            print(f"    [邮件] 收件人: {to_addr[:40]}... | 时间: {mail_time_str}", flush=True)

            # 检查邮件时间（必须在 since_time 之后，且在3分钟内）
            if since_time and mail_time_local:
                since_time_str = since_time.strftime("%H:%M:%S")

                # 邮件必须在 since_time 之后（允许5秒容差，考虑网络延迟和时钟误差）
                time_diff = (mail_time_local - since_time).total_seconds()
                if time_diff < -5:  # 邮件比请求早超过5秒才跳过
                    print(f"    [跳过] 邮件时间 {mail_time_str} 早于请求时间 {since_time_str} (差 {int(-time_diff)} 秒)", flush=True)
                    return None

                # 邮件不能超过3分钟
                now = datetime.now()
                age_seconds = (now - mail_time_local).total_seconds()
                if age_seconds > 180:
                    print(f"    [跳过] 邮件已过期 ({int(age_seconds)}秒 > 180秒)", flush=True)
                    return None

            # 检查收件人（To 字段必须包含目标邮箱）
            if target_email:
                if target_email.lower() not in to_addr.lower():
                    print(f"    [跳过] 收件人不匹配 (目标: {target_email})", flush=True)
                    return None

            # 获取邮件正文
            body = self._get_email_body(msg)

            # 精确匹配：检查邮件内容中是否包含目标邮箱
            if target_email:
                if target_email.lower() not in body.lower():
                    print(f"    [跳过] 邮件正文不包含目标邮箱 {target_email}", flush=True)
                    return None

            # 提取验证码（忽略大小写匹配）
            for pattern in self.VERIFICATION_CODE_PATTERNS:
                match = re.search(pattern, body, re.IGNORECASE)
                if match:
                    code = match.group(1).upper()  # 统一转为大写
                    print(f"    [OK] 验证码: {code} (目标: {target_email or '任意'})")
                    logger.info(f"提取到验证码: {code}")
                    return code

            # 调试输出：打印邮件正文前500字符，帮助分析验证码格式
            body_preview = body[:500].replace('\n', '\\n').replace('\r', '\\r')
            print(f"    [调试] 邮件正文预览: {body_preview}", flush=True)
            print(f"    [跳过] 未找到验证码格式", flush=True)
            return None

        except Exception as e:
            print(f"    [错误] 解析邮件失败: {e}", flush=True)
            logger.debug(f"解析邮件失败: {e}")
            return None

    def _delete_email_sync(self, msg_id: bytes):
        """同步删除邮件（内部方法）"""
        try:
            self._connection.store(msg_id, '+FLAGS', '\\Deleted')
        except:
            pass

    def _decode_header_value(self, value: str) -> str:
        """解码邮件头部值"""
        if not value:
            return ""
        try:
            decoded_parts = decode_header(value)
            result = ""
            for part, charset in decoded_parts:
                if isinstance(part, bytes):
                    charset = charset or "utf-8"
                    result += part.decode(charset, errors="ignore")
                else:
                    result += part
            return result
        except:
            return value

    def _get_email_body(self, msg: email.message.Message) -> str:
        """获取邮件正文"""
        body = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body += payload.decode(charset, errors="ignore")
                elif content_type == "text/html" and not body:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        html = payload.decode(charset, errors="ignore")
                        body = re.sub(r'<[^>]+>', ' ', html)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="ignore")

        return body

    # 保留同步方法的兼容性别名（供非异步上下文使用）
    def fetch_verification_code_sync(
        self,
        timeout: int = 120,
        poll_interval: int = 2,
        since_time: Optional[datetime] = None,
        target_email: Optional[str] = None
    ) -> Optional[str]:
        """
        同步获取 Google 验证码（兼容旧代码）

        注意：此方法会阻塞，建议在非异步上下文或独立线程中使用
        """
        if since_time is None:
            since_time = datetime.now() - timedelta(minutes=2)

        if not self._connection:
            if not self._connect_sync():
                return None

        if target_email:
            print(f"  [邮箱] 正在获取 {target_email} 的验证码...")
            print(f"  [邮箱] 请求时间: {since_time.strftime('%H:%M:%S')}")
        else:
            print(f"  [邮箱] 正在获取验证码...")

        start_time = time.time()
        poll_count = 0

        while time.time() - start_time < timeout:
            poll_count += 1

            if not self._refresh_inbox_sync():
                if not self._connect_sync():
                    print(f"  [邮箱] IMAP连接失败，重试中...")
                    time.sleep(1)
                    continue

            code, msg_id = self._search_verification_email_sync(target_email, since_time)
            if code:
                self._delete_email_sync(msg_id)
                return code

            elapsed = int(time.time() - start_time)
            print(f"  [邮箱] 等待验证码... ({elapsed}秒/{timeout}秒) [轮询#{poll_count}]")
            time.sleep(poll_interval)

        print(f"  [邮箱] 获取验证码超时 ({timeout}秒)")
        return None
