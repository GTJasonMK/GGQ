"""
并发自动登录服务

支持多账号并发刷新，大幅提升刷新速度
"""
import asyncio
import re
import random
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Set
from urllib.parse import urlparse, parse_qs

from app.config import AccountConfig, config_manager

logger = logging.getLogger(__name__)


class VerificationCodeHub:
    """
    验证码中心

    持续轮询邮箱，按目标邮箱精确匹配验证码。
    每封验证邮件都有明确的收件人，用于匹配等待者。
    """

    # 验证码正则模式（和 email_service.py 一致）
    CODE_PATTERNS = [
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
        # 尝试匹配任何被空白包围的6位验证码
        r'(?:验证码|code|Code)[^\d]*(\d{6})',
    ]

    def __init__(self, email_config: dict):
        """
        初始化验证码中心

        Args:
            email_config: QQ 邮箱配置
        """
        self.email_config = email_config
        self._imap_client = None
        self._running = False
        self._poll_task = None

        # 按目标邮箱存储验证码：{target_email_lower: [(code, timestamp), ...]}
        self._codes_by_email: Dict[str, List[tuple]] = {}
        # 后备队列（无法识别收件人时使用）
        self._fallback_queue: List[tuple] = []
        # 全局通知事件（有新验证码时通知所有等待者）
        self._new_code_event = asyncio.Event()
        # 已处理的邮件 UID
        self._processed_uids: Set[int] = set()
        # 锁
        self._lock = asyncio.Lock()
        # 等待者计数（用于日志）
        self._waiting_count = 0
        # 暂停标志（用于让其他服务使用 IMAP）
        self._paused = False

    async def start(self):
        """启动验证码轮询"""
        if self._running:
            return

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        print(f"  [CodeHub] Started, polling every 2s")

    async def stop(self):
        """停止轮询"""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self._disconnect()
        print(f"  [CodeHub] Stopped")

    async def pause(self):
        """暂停轮询（释放 IMAP 连接供其他服务使用）"""
        if self._paused:
            return
        self._paused = True
        await self._disconnect()
        print(f"  [CodeHub] Paused - IMAP connection released")

    async def resume(self):
        """恢复轮询"""
        if not self._paused:
            return
        self._paused = False
        print(f"  [CodeHub] Resumed")

    async def _connect(self):
        """连接 IMAP"""
        if self._imap_client:
            return True

        try:
            import aioimaplib

            imap_server = self.email_config.get("imap_server", "imap.qq.com")
            imap_port = self.email_config.get("imap_port", 993)
            address = self.email_config.get("address", "")

            print(f"  [CodeHub] Connecting to {imap_server}:{imap_port} as {address}...")

            self._imap_client = aioimaplib.IMAP4_SSL(
                host=imap_server,
                port=imap_port
            )
            await self._imap_client.wait_hello_from_server()

            # 登录并检查状态
            login_response = await self._imap_client.login(
                address,
                self.email_config.get("auth_code", "")
            )
            if not login_response or 'OK' not in login_response[0]:
                print(f"  [CodeHub] Login failed: {login_response}")
                self._imap_client = None
                return False

            # 选择收件箱并检查状态
            select_response = await self._imap_client.select("INBOX")
            if not select_response or 'OK' not in select_response[0]:
                print(f"  [CodeHub] Select INBOX failed: {select_response}")
                self._imap_client = None
                return False

            print(f"  [CodeHub] IMAP connected successfully")
            return True

        except Exception as e:
            logger.error(f"IMAP connection failed: {e}")
            print(f"  [CodeHub] IMAP connection failed: {e}")
            self._imap_client = None
            return False

    async def _disconnect(self):
        """断开连接"""
        if self._imap_client:
            try:
                await self._imap_client.logout()
            except:
                pass
            self._imap_client = None

    async def _poll_loop(self):
        """轮询循环"""
        poll_count = 0
        idle_count = 0  # 空闲计数
        while self._running:
            try:
                poll_count += 1

                # 暂停时，等待恢复
                if self._paused:
                    await asyncio.sleep(1)
                    continue

                # 没有等待者时，减少轮询频率并断开连接
                if self._waiting_count == 0:
                    idle_count += 1
                    if idle_count == 1:
                        # 首次进入空闲，打印一次并断开连接
                        print(f"  [CodeHub] No waiters, entering idle mode...")
                        await self._disconnect()
                    # 空闲时每 10 秒检查一次（而非 2 秒）
                    await asyncio.sleep(10)
                    continue

                # 有等待者，重置空闲计数
                idle_count = 0

                if poll_count % 10 == 1:  # 每20秒打印一次状态
                    total_codes = sum(len(v) for v in self._codes_by_email.values()) + len(self._fallback_queue)
                    print(f"  [CodeHub] Polling... (count={poll_count}, codes={total_codes}, waiting={self._waiting_count})")
                await self._poll_once()
                await asyncio.sleep(2)  # 每 2 秒轮询一次
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Poll error: {e}")
                print(f"  [CodeHub] Poll loop error: {e}")
                await self._disconnect()
                await asyncio.sleep(5)

    async def _poll_once(self):
        """单次轮询"""
        if not await self._connect():
            print("  [CodeHub] IMAP connection failed")
            return

        try:
            # 按发件人搜索 Google 验证邮件
            # aioimaplib 的 search 语法: 整个搜索条件作为单个字符串
            response = await self._imap_client.search('FROM "noreply-googlecloud@google.com"')

            # aioimaplib 返回 (status, data) 格式
            if not response or len(response) < 2:
                return

            status, data = response[0], response[1]

            # 检查响应状态
            if 'OK' not in status:
                print(f"  [CodeHub] Search failed: {status}")
                return

            # data 可能是 list 或 bytes
            if not data:
                return

            # 解析 UID 列表
            if isinstance(data, (list, tuple)) and data:
                uid_str = data[0] if isinstance(data[0], str) else data[0].decode() if data[0] else ""
            elif isinstance(data, bytes):
                uid_str = data.decode()
            elif isinstance(data, str):
                uid_str = data
            else:
                return

            uids = uid_str.split()
            if not uids:
                return

            # 过滤掉已处理的
            new_uids = [uid for uid in uids[-20:] if int(uid) not in self._processed_uids]
            if new_uids:
                print(f"  [CodeHub] Found {len(new_uids)} new Google emails to process")

            for uid in new_uids:
                uid_int = int(uid)
                await self._process_email(uid)
                self._processed_uids.add(uid_int)

                # 清理旧的 UID 记录
                if len(self._processed_uids) > 1000:
                    self._processed_uids = set(list(self._processed_uids)[-500:])

        except Exception as e:
            import traceback
            error_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}: {repr(e)}"
            logger.error(f"Poll once error: {error_msg}\n{traceback.format_exc()}")
            print(f"  [CodeHub] Poll error: {error_msg}")
            # 发生错误时断开连接，下次轮询会重连
            await self._disconnect()

    async def _process_email(self, uid: str):
        """处理单封邮件（已经按发件人过滤过了）"""
        try:
            response = await self._imap_client.fetch(uid, "(RFC822)")

            # aioimaplib 返回 (status, data_list) 格式
            if not response or len(response) < 2:
                return

            status, data = response[0], response[1]

            if 'OK' not in status or not data:
                return

            # 解析邮件
            import email
            from email.header import decode_header
            from email.utils import parsedate_to_datetime

            # aioimaplib 返回格式与 imaplib 不同：
            # data 是一个 list，其中 bytearray 类型是邮件内容
            raw_email = None
            for item in data:
                # aioimaplib 返回 bytearray 作为邮件内容
                if isinstance(item, bytearray):
                    raw_email = bytes(item).decode("utf-8", errors="ignore")
                    break
                # 兼容 imaplib 的 tuple 格式
                elif isinstance(item, tuple) and len(item) >= 2:
                    raw_email = item[1]
                    if isinstance(raw_email, bytes):
                        raw_email = raw_email.decode("utf-8", errors="ignore")
                    break
                # 如果是 bytes 类型，直接解码
                elif isinstance(item, bytes) and b'@' in item:
                    raw_email = item.decode("utf-8", errors="ignore")
                    break

            if not raw_email:
                print(f"  [CodeHub] Email {uid}: no raw_email found in data")
                return

            msg = email.message_from_string(raw_email)

            # 获取邮件时间
            date_str = msg.get("Date", "")
            mail_time_str = "unknown"
            mail_time_local = None

            if date_str:
                try:
                    mail_time = parsedate_to_datetime(date_str)
                    if mail_time.tzinfo:
                        mail_time_local = mail_time.astimezone().replace(tzinfo=None)
                    else:
                        mail_time_local = mail_time
                    mail_time_str = mail_time_local.strftime("%H:%M:%S")
                except:
                    pass

            # 检查邮件是否太旧（超过5分钟的跳过）
            if mail_time_local:
                age_seconds = (datetime.now() - mail_time_local).total_seconds()
                if age_seconds > 300:
                    print(f"  [CodeHub] Email {uid}: too old ({int(age_seconds)}s), skipped")
                    return

            # 获取主题用于日志
            subject = ""
            if msg["Subject"]:
                decoded = decode_header(msg["Subject"])
                subject = "".join(
                    part.decode(enc or "utf-8") if isinstance(part, bytes) else part
                    for part, enc in decoded
                )

            print(f"  [CodeHub] Email {uid}: time={mail_time_str}, subject='{subject[:40]}...'")

            # 获取邮件正文
            body = self._get_email_body(msg)
            if not body:
                print(f"  [CodeHub] Email {uid}: no body content")
                return

            # 获取收件人（优先从 To 头，其次从正文）
            to_header = msg.get("To", "")
            target_email = self._extract_email_from_header(to_header)

            # 如果 To 头是转发邮箱（如 QQ 邮箱），则从正文中提取真正的目标邮箱
            if target_email and ("qq.com" in target_email or "163.com" in target_email or "126.com" in target_email):
                # 这是转发邮箱，需要从正文提取真正的目标
                body_email = self._extract_target_email_from_body(body)
                if body_email:
                    print(f"  [CodeHub] Email {uid}: recipient from body={body_email}")
                    target_email = body_email
                else:
                    print(f"  [CodeHub] Email {uid}: forwarded to {target_email}, but no target in body")
            elif target_email:
                print(f"  [CodeHub] Email {uid}: recipient={target_email}")
            else:
                # 尝试从 X-Original-To 或 Delivered-To 获取
                target_email = self._extract_email_from_header(msg.get("X-Original-To", ""))
                if not target_email:
                    target_email = self._extract_email_from_header(msg.get("Delivered-To", ""))

                # 最后尝试从正文提取
                if not target_email:
                    target_email = self._extract_target_email_from_body(body)

                if target_email:
                    print(f"  [CodeHub] Email {uid}: recipient={target_email} (from alt source)")

            # 提取验证码
            code = self._extract_code(body)

            if code:
                await self._store_code(code, target_email)
                total_codes = sum(len(v) for v in self._codes_by_email.values()) + len(self._fallback_queue)
                print(f"  [CodeHub] SUCCESS: code={code} for={target_email or 'unknown'} (total={total_codes}, waiting={self._waiting_count})")
                logger.info(f"Got code: {code} for {target_email or 'unknown'}")
            else:
                print(f"  [CodeHub] Email {uid}: no code found")
                # 打印正文前300字符帮助调试
                body_preview = body[:300].replace('\n', '\\n')
                print(f"  [CodeHub] Body: {body_preview}")

        except Exception as e:
            logger.error(f"Process email error: {e}")
            print(f"  [CodeHub] Email {uid} error: {e}")

    def _get_email_body(self, msg) -> str:
        """获取邮件正文"""
        body = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body += payload.decode("utf-8", errors="ignore")
                elif content_type == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        # 简单去除 HTML 标签
                        html = payload.decode("utf-8", errors="ignore")
                        body += re.sub(r'<[^>]+>', ' ', html)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode("utf-8", errors="ignore")

        return body

    def _extract_code(self, body: str) -> Optional[str]:
        """提取验证码"""
        for pattern in self.CODE_PATTERNS:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                code = match.group(1).upper()
                # 验证是否为有效的 6 位验证码
                if len(code) == 6 and code.isalnum():
                    return code
        return None

    def _extract_email_from_header(self, header: str) -> Optional[str]:
        """从邮件头中提取邮箱地址"""
        if not header:
            return None
        # 匹配邮箱地址（支持 "Name <email@domain>" 格式）
        match = re.search(r'[\w\.\-\+]+@[\w\.\-]+\.\w+', header)
        if match:
            return match.group(0).lower()
        return None

    def _extract_target_email_from_body(self, body: str) -> Optional[str]:
        """
        从邮件正文中提取目标邮箱地址

        Google 验证邮件通常包含目标账号，如：
        - "您的账号 xxx@domain.com 的验证码是..."
        - "...for xxx@domain.com..."
        """
        if not body:
            return None

        # 排除常见的转发/发送方邮箱
        excluded_domains = [
            'qq.com', '163.com', '126.com', 'gmail.com',
            'google.com', 'googlemail.com', 'outlook.com', 'hotmail.com'
        ]

        # 找出所有邮箱地址
        emails = re.findall(r'[\w\.\-\+]+@[\w\.\-]+\.\w+', body.lower())

        # 返回第一个非排除域名的邮箱
        for email in emails:
            domain = email.split('@')[1] if '@' in email else ''
            if domain and domain not in excluded_domains:
                return email

        return None

    async def _store_code(self, code: str, target_email: str = None):
        """存储验证码（按目标邮箱分类）"""
        async with self._lock:
            timestamp = datetime.now().timestamp()

            if target_email:
                target_lower = target_email.lower()
                # 检查是否重复
                if target_lower in self._codes_by_email:
                    for existing_code, ts in self._codes_by_email[target_lower]:
                        if existing_code == code and timestamp - ts < 300:
                            logger.debug(f"Duplicate code ignored: {code} for {target_email}")
                            return
                else:
                    self._codes_by_email[target_lower] = []

                # 添加到对应邮箱的队列
                self._codes_by_email[target_lower].append((code, timestamp))
            else:
                # 无法识别收件人，放入后备队列
                for existing_code, ts in self._fallback_queue:
                    if existing_code == code and timestamp - ts < 300:
                        logger.debug(f"Duplicate code ignored: {code}")
                        return
                self._fallback_queue.append((code, timestamp))

            # 通知所有等待者
            self._new_code_event.set()

    async def wait_for_code(
        self,
        target_email: str = None,
        timeout: float = 120,
        since_time: Optional[datetime] = None
    ) -> Optional[str]:
        """
        等待验证码（按目标邮箱精确匹配）

        优先从目标邮箱的队列获取验证码，如果没有则尝试后备队列。

        Args:
            target_email: 目标邮箱（用于精确匹配）
            timeout: 超时时间（秒）
            since_time: 只接受此时间之后的验证码

        Returns:
            验证码，超时返回 None
        """
        since_ts = since_time.timestamp() if since_time else 0
        target_lower = target_email.lower() if target_email else None

        # 增加等待者计数
        self._waiting_count += 1
        tag = f"[{target_email.split('@')[0] if target_email else 'unknown'}]"

        try:
            end_time = asyncio.get_event_loop().time() + timeout
            logger.info(f"{tag} Start waiting for code (since_ts={since_ts:.0f})")

            while asyncio.get_event_loop().time() < end_time:
                async with self._lock:
                    # 优先从目标邮箱的队列获取
                    if target_lower and target_lower in self._codes_by_email:
                        codes = self._codes_by_email[target_lower]
                        for i, (code, ts) in enumerate(codes):
                            if ts > since_ts:
                                codes.pop(i)
                                # 清理空队列
                                if not codes:
                                    del self._codes_by_email[target_lower]
                                logger.info(f"{tag} Got code: {code} (exact match)")
                                return code

                    # 后备：从通用队列获取（按时间顺序）
                    for i, (code, ts) in enumerate(self._fallback_queue):
                        if ts > since_ts:
                            self._fallback_queue.pop(i)
                            logger.info(f"{tag} Got code: {code} (from fallback queue)")
                            return code

                # 等待新验证码
                remaining = end_time - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break

                # 等待通知或超时
                self._new_code_event.clear()
                try:
                    await asyncio.wait_for(
                        self._new_code_event.wait(),
                        timeout=min(5, remaining)
                    )
                except asyncio.TimeoutError:
                    pass

            logger.warning(f"{tag} Timeout, no code received")
            return None

        finally:
            self._waiting_count -= 1

    def cleanup_old_codes(self, max_age: float = 600):
        """清理过期验证码"""
        now = datetime.now().timestamp()
        # 清理各邮箱队列
        for email in list(self._codes_by_email.keys()):
            self._codes_by_email[email] = [
                (code, ts) for code, ts in self._codes_by_email[email]
                if now - ts < max_age
            ]
            if not self._codes_by_email[email]:
                del self._codes_by_email[email]
        # 清理后备队列
        self._fallback_queue = [
            (code, ts) for code, ts in self._fallback_queue
            if now - ts < max_age
        ]

    def get_status(self) -> dict:
        """获取验证码中心状态"""
        total_codes = sum(len(v) for v in self._codes_by_email.values()) + len(self._fallback_queue)
        return {
            "running": self._running,
            "total_codes": total_codes,
            "codes_by_email": {k: len(v) for k, v in self._codes_by_email.items()},
            "fallback_queue_size": len(self._fallback_queue),
            "waiting_count": self._waiting_count,
            "processed_uids": len(self._processed_uids),
        }


class ConcurrentAutoLoginService:
    """
    并发自动登录服务

    支持多账号同时刷新，大幅提升效率
    """

    def __init__(self, config: dict, max_concurrent: int = 5):
        """
        初始化服务

        Args:
            config: 自动登录配置
            max_concurrent: 最大并发数
        """
        self.config = config
        self.max_concurrent = max_concurrent
        self.qq_email_config = config.get("qq_email", {})
        self.verification_timeout = config.get("verification_timeout", 120)
        self.headless = config.get("headless", True)
        self.yescaptcha_api_key = config.get("yescaptcha_api_key", "")

        self._playwright = None
        self._browser = None
        self._code_hub: Optional[VerificationCodeHub] = None
        self._captcha_service = None
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def initialize(self):
        """初始化服务"""
        # 启动浏览器
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        
        launch_options = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--disable-infobars",
            ]
        }
        
        # 如果配置了代理，添加到启动参数
        proxy_url = self.config.get("proxy")
        if proxy_url:
            launch_options["proxy"] = {"server": proxy_url}
            print(f"  [并发服务] 使用代理: {proxy_url}")
            
        self._browser = await self._playwright.chromium.launch(**launch_options)

        # 启动验证码中心
        self._code_hub = VerificationCodeHub(self.qq_email_config)
        await self._code_hub.start()

        # 初始化打码服务
        if self.yescaptcha_api_key:
            from .captcha_service import YesCaptchaService
            self._captcha_service = YesCaptchaService(self.yescaptcha_api_key)

        # 并发控制
        self._semaphore = asyncio.Semaphore(self.max_concurrent)

        print(f"  [并发服务] 已初始化 (最大并发={self.max_concurrent})")

    async def close(self):
        """关闭服务"""
        if self._code_hub:
            await self._code_hub.stop()
        if self._captcha_service:
            await self._captcha_service.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def refresh_accounts(
        self,
        accounts: List[tuple]  # [(AccountConfig, google_email), ...]
    ) -> Dict[str, Any]:
        """
        并发刷新多个账号

        Args:
            accounts: 账号列表 [(AccountConfig, google_email), ...]

        Returns:
            刷新结果
        """
        if not self._browser:
            await self.initialize()

        print(f"\n{'='*60}")
        print(f"  [并发刷新] 开始刷新 {len(accounts)} 个账号 (并发={self.max_concurrent})")
        print(f"{'='*60}")

        # 创建刷新任务
        tasks = []
        for account, google_email in accounts:
            task = asyncio.create_task(
                self._refresh_one_account(account, google_email)
            )
            tasks.append((account, google_email, task))

        # 等待所有任务完成
        results = {
            "success": [],
            "failed": [],
            "total": len(accounts),
        }

        for account, google_email, task in tasks:
            try:
                credentials = await task
                if credentials:
                    results["success"].append({
                        "email": google_email,
                        "credentials": credentials
                    })
                    print(f"  [成功] {google_email}")
                else:
                    results["failed"].append({
                        "email": google_email,
                        "error": "刷新失败"
                    })
                    print(f"  [失败] {google_email}")
                    # 记录刷新失败
                    try:
                        from app.services.account_pool_service import account_pool_service
                        account_pool_service.record_refresh_failure(account.note)
                    except:
                        pass
            except Exception as e:
                results["failed"].append({
                    "email": google_email,
                    "error": str(e)
                })
                print(f"  [错误] {google_email}: {e}")
                # 记录刷新失败
                try:
                    from app.services.account_pool_service import account_pool_service
                    account_pool_service.record_refresh_failure(account.note)
                except:
                    pass

        print(f"\n{'='*60}")
        print(f"  [并发刷新] 完成: 成功={len(results['success'])}, 失败={len(results['failed'])}")
        print(f"{'='*60}\n")

        return results

    async def _refresh_one_account(
        self,
        account: AccountConfig,
        google_email: str
    ) -> Optional[Dict[str, Any]]:
        """刷新单个账号（带并发控制）"""
        async with self._semaphore:
            return await self._do_refresh(account, google_email)

    async def _do_refresh(
        self,
        account: AccountConfig,
        google_email: str
    ) -> Optional[Dict[str, Any]]:
        """执行实际的刷新逻辑"""
        from .service import (
            GoogleAutoLogin, _safe_goto, _handle_trial_signup_page,
            _dismiss_welcome_dialog, inject_stealth_scripts
        )
        from .human_behavior import HumanBehavior

        context = None
        page = None

        try:
            # 创建独立的浏览器上下文
            context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )

            # 注入反检测脚本
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                window.chrome = { runtime: {} };
            """)

            page = await context.new_page()
            await inject_stealth_scripts(page)

            # 访问目标页面
            target_url = f"https://business.gemini.google/home/cid/{account.team_id}"
            if account.csesidx:
                target_url += f"?csesidx={account.csesidx}"

            print(f"    [{google_email}] 访问目标页面...")
            await _safe_goto(page, target_url, wait_until="networkidle", timeout=60000)

            # 预热
            human = HumanBehavior(page)
            await human.warm_up_session(duration=3)

            current_url = page.url

            # 检查是否需要登录
            need_login = (
                "accounts.google.com" in current_url or
                "auth.business.gemini.google" in current_url
            )

            if need_login:
                print(f"    [{google_email}] 需要登录...")

                # 使用并发友好的登录流程
                login_success = await self._concurrent_login(
                    page, human, google_email
                )

                if not login_success:
                    print(f"    [{google_email}] 登录失败")
                    return None

                # 重新访问
                await _safe_goto(page, target_url, wait_until="networkidle", timeout=60000)
                await asyncio.sleep(2)
                current_url = page.url

            # 处理首次注册
            if "admin/create" in current_url:
                display_name = account.note or google_email.split("@")[0]
                await _handle_trial_signup_page(page, display_name)
                await asyncio.sleep(3)
                current_url = page.url

            # 等待进入聊天页面
            for _ in range(30):
                if "/cid/" in page.url:
                    break
                await asyncio.sleep(1)

            # 关闭弹窗
            await _dismiss_welcome_dialog(page)

            # 提取凭证
            current_url = page.url
            if "business.gemini.google" not in current_url or "/cid/" not in current_url:
                print(f"    [{google_email}] 未能进入聊天页面")
                return None

            credentials = {}

            # 提取 team_id
            cid_match = re.search(r'/cid/([^/?#]+)', current_url)
            if cid_match:
                credentials["team_id"] = cid_match.group(1)

            # 提取 csesidx
            parsed = urlparse(current_url)
            params = parse_qs(parsed.query)
            if "csesidx" in params:
                credentials["csesidx"] = params["csesidx"][0]

            # 提取 Cookies
            cookies = await context.cookies("https://business.gemini.google")
            for cookie in cookies:
                if cookie["name"] == "__Secure-C_SES":
                    credentials["secure_c_ses"] = cookie["value"]
                elif cookie["name"] == "__Host-C_OSES":
                    credentials["host_c_oses"] = cookie["value"]

            if credentials.get("secure_c_ses"):
                credentials["refresh_time"] = datetime.now().isoformat()
                print(f"    [{google_email}] 凭证提取成功")
                return credentials
            else:
                print(f"    [{google_email}] 未能获取凭证")
                return None

        except Exception as e:
            logger.error(f"刷新账号出错 [{google_email}]: {e}")
            return None

        finally:
            if page:
                await page.close()
            if context:
                await context.close()

    async def _concurrent_login(
        self,
        page,
        human: 'HumanBehavior',
        google_email: str
    ) -> bool:
        """
        并发友好的登录流程

        使用验证码中心获取验证码，而不是独占式轮询
        """
        from .captcha_service import CaptchaInterceptor

        try:
            current_url = page.url

            # 输入邮箱
            if "auth.business.gemini.google" in current_url:
                email_input = await page.query_selector('#email-input')
                if email_input:
                    await human.type_like_human(email_input, google_email)
                    await asyncio.sleep(0.5)

                    login_btn = await page.query_selector('#log-in-button')
                    if login_btn:
                        await human.human_click(login_btn)
                    else:
                        await page.keyboard.press("Enter")

            elif "accounts.google.com" in current_url:
                email_input = await page.query_selector('input[type="email"]')
                if email_input:
                    await human.type_like_human(email_input, google_email)
                    await asyncio.sleep(0.5)
                    await page.keyboard.press("Enter")

            # 等待跳转到验证码页面
            await asyncio.sleep(3)

            # 检查是否需要验证码
            for _ in range(15):
                current_url = page.url
                if "accountverification.business.gemini.google" in current_url:
                    break
                if "business.gemini.google/home" in current_url:
                    return True  # 无需验证码
                await asyncio.sleep(1)

            if "accountverification" not in page.url:
                return False

            # 启动验证码拦截（如果配置了打码服务）
            interceptor = None
            if self._captcha_service:
                interceptor = CaptchaInterceptor(page, self._captcha_service)
                await interceptor.start_monitoring()

            try:
                # 记录请求时间
                request_time = datetime.now()

                # 等待验证码发送
                print(f"    [{google_email}] Waiting for verification code...")
                await asyncio.sleep(5)

                # 从验证码中心获取验证码
                code = await self._code_hub.wait_for_code(
                    target_email=google_email,
                    timeout=self.verification_timeout,
                    since_time=request_time
                )

                if not code:
                    print(f"    [{google_email}] No code received")
                    return False

                print(f"    [{google_email}] Got code: {code}")

                # 输入验证码
                inputs = await page.query_selector_all('input[type="text"]')
                visible_inputs = [inp for inp in inputs if await inp.is_visible()]

                if len(visible_inputs) >= 6:
                    for i, char in enumerate(code[:6]):
                        await visible_inputs[i].fill(char)
                        await asyncio.sleep(0.1)
                elif len(visible_inputs) == 1:
                    await visible_inputs[0].fill(code)

                # 点击验证按钮
                verify_btn = await page.query_selector('button:has-text("验证")')
                if verify_btn:
                    await verify_btn.click()
                else:
                    await page.keyboard.press("Enter")

                await asyncio.sleep(3)

                # 检查是否成功
                for _ in range(30):
                    current_url = page.url
                    if "business.gemini.google" in current_url and "auth" not in current_url:
                        return True
                    if "admin/create" in current_url:
                        return True
                    await asyncio.sleep(1)

                return False

            finally:
                if interceptor:
                    interceptor.stop_monitoring()

        except Exception as e:
            logger.error(f"登录出错 [{google_email}]: {e}")
            return False


# 全局并发服务实例
concurrent_login_service: Optional[ConcurrentAutoLoginService] = None


async def get_concurrent_service(config: dict = None) -> ConcurrentAutoLoginService:
    """获取并发服务实例"""
    global concurrent_login_service

    if concurrent_login_service is None:
        if config is None:
            auto_login_config = config_manager.config.auto_login
            if not auto_login_config:
                raise ValueError("未配置 auto_login")
            config = {
                "enabled": auto_login_config.enabled,
                "qq_email": {
                    "address": auto_login_config.qq_email.address,
                    "auth_code": auto_login_config.qq_email.auth_code,
                    "imap_server": auto_login_config.qq_email.imap_server,
                    "imap_port": auto_login_config.qq_email.imap_port,
                },
                "verification_timeout": auto_login_config.verification_timeout,
                "headless": getattr(auto_login_config, 'headless', True),
                "yescaptcha_api_key": getattr(auto_login_config, 'yescaptcha_api_key', ''),
                "proxy": config_manager.config.proxy,
            }

        concurrent_login_service = ConcurrentAutoLoginService(config)
        await concurrent_login_service.initialize()

    return concurrent_login_service
