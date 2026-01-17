"""
凭证服务模块
- 凭证有效性验证
- 凭证自动刷新
- 异步后台刷新队列
"""
import asyncio
import sys
import time
import json
import logging
from typing import Optional, Tuple, Set, Dict, List
from datetime import datetime
from pathlib import Path

import httpx

# 添加 backend 目录到路径，以便导入统一配置
BACKEND_DIR = Path(__file__).parent.parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 导入统一配置
try:
    import config as unified_config
except ImportError:
    unified_config = None

from app.config import config_manager, AccountConfig
from app.utils.crypto import decode_xsrf_token, create_jwt_token

logger = logging.getLogger(__name__)

# 并发刷新服务（延迟导入避免循环依赖）
_concurrent_service = None

# API URL
GETOXSRF_URL = "https://business.gemini.google/auth/getoxsrf"
CREATE_SESSION_URL = "https://biz-discoveryengine.googleapis.com/v1alpha/locations/global/widgetCreateSession"
CHAT_API_URL = "https://biz-discoveryengine.googleapis.com/v1/projects/gemini-business-ccai/locations/us/collections/default_collection/engines/gemini-business-runtime/chats"


async def verify_credential(
    account: AccountConfig,
    proxy: Optional[str] = None,
    timeout: float = 30.0,
    full_verify: bool = True
) -> Tuple[bool, str]:
    """
    验证账号凭证是否有效

    通过调用 getoxsrf API 验证 Cookie 是否有效，
    可选进行完整验证（生成 JWT 并调用聊天 API）

    Args:
        account: 账号配置
        proxy: 代理地址
        timeout: 超时时间
        full_verify: 是否进行完整验证（包括 JWT 生成和 API 调用）

    Returns:
        (is_valid, error_message)
    """
    if not account.secure_c_ses or not account.csesidx:
        return False, "缺少必要凭据 (secure_c_ses 或 csesidx)"

    try:
        async with httpx.AsyncClient(
            proxy=proxy,
            verify=False,
            timeout=timeout
        ) as client:
            # 步骤1：获取 xsrfToken 和 keyId
            url = f"{GETOXSRF_URL}?csesidx={account.csesidx}"
            headers = {
                "accept": "*/*",
                "user-agent": account.user_agent,
                "cookie": f"__Secure-C_SES={account.secure_c_ses}; __Host-C_OSES={account.host_c_oses}",
            }

            resp = await client.get(url, headers=headers)

            if resp.status_code == 401:
                return False, "认证失败 (401) - Cookie 已过期"
            elif resp.status_code == 403:
                return False, "访问被拒绝 (403) - 需要重新登录"
            elif resp.status_code != 200:
                return False, f"请求失败 ({resp.status_code})"

            # 解析响应
            text = resp.text
            if text.startswith(")]}'\n") or text.startswith(")]}'"):
                text = text[4:].strip()

            try:
                data = json.loads(text)
                xsrf_token = data.get("xsrfToken")
                key_id = data.get("keyId")

                if not xsrf_token or not key_id:
                    return False, "响应中缺少 keyId 或 xsrfToken"

            except json.JSONDecodeError:
                return False, "无法解析 getoxsrf 响应"

            # 如果不需要完整验证，到这里就可以返回
            if not full_verify:
                return True, ""

            # 步骤2：生成 JWT
            try:
                key_bytes = decode_xsrf_token(xsrf_token)
                jwt_token, _ = create_jwt_token(key_bytes, key_id, account.csesidx)
            except Exception as e:
                return False, f"JWT 生成失败: {e}"

            # 步骤3：尝试创建会话来验证 JWT
            try:
                import uuid
                session_id = f"test_{uuid.uuid4().hex[:8]}"
                body = {
                    "configId": account.team_id,
                    "additionalParams": {"token": "-"},
                    "createSessionRequest": {
                        "session": {"name": session_id, "displayName": session_id}
                    }
                }

                api_headers = {
                    "accept": "*/*",
                    "authorization": f"Bearer {jwt_token}",
                    "content-type": "application/json",
                    "origin": "https://business.gemini.google",
                    "referer": "https://business.gemini.google/",
                    "user-agent": account.user_agent,
                }

                api_resp = await client.post(
                    CREATE_SESSION_URL,
                    headers=api_headers,
                    json=body,
                    timeout=20.0
                )

                if api_resp.status_code == 401:
                    return False, "API 认证失败 (401) - JWT 无效"
                elif api_resp.status_code == 403:
                    return False, "API 访问被拒绝 (403)"
                elif api_resp.status_code == 429:
                    # 限流但凭证有效
                    return True, ""
                elif api_resp.status_code not in [200, 201]:
                    # 其他错误，但 getoxsrf 成功说明凭证可能有效
                    logger.warning(f"API 调用失败 ({api_resp.status_code})，但凭证验证通过")
                    return True, ""

                # 验证成功
                logger.debug("完整凭证验证成功")
                return True, ""

            except httpx.TimeoutException:
                # API 超时，但 getoxsrf 成功说明凭证有效
                logger.warning("API 调用超时，但凭证验证通过")
                return True, ""
            except Exception as e:
                logger.warning(f"API 调用异常: {e}，但凭证验证通过")
                return True, ""

    except httpx.TimeoutException:
        return False, "请求超时 - 检查网络或代理设置"
    except httpx.ProxyError as e:
        return False, f"代理错误 - {e}"
    except Exception as e:
        return False, f"验证失败: {e}"


class CredentialRefreshService:
    """
    凭证刷新服务

    负责：
    1. 检测凭证有效性
    2. 自动刷新失效凭证
    3. 更新配置文件
    4. 并发后台刷新队列（最多5个并发）
    """

    def __init__(self, max_concurrent: int = None):
        # 从统一配置读取 max_concurrent，如果没有则使用默认值
        if max_concurrent is None:
            if unified_config and hasattr(unified_config, 'ACCOUNT_POOL_MAX_CONCURRENT'):
                max_concurrent = unified_config.ACCOUNT_POOL_MAX_CONCURRENT
            else:
                max_concurrent = 5

        self._lock = asyncio.Lock()
        self._refreshing: Set[int] = set()  # 正在刷新的账号索引
        self._refresh_queue: asyncio.Queue = None  # 刷新队列
        self._queued_accounts: Set[int] = set()  # 已在队列中的账号（防止重复加入）
        self._auto_login_service = None
        self._initialized = False
        self._background_task: Optional[asyncio.Task] = None
        self._invalid_accounts: Set[int] = set()  # 已知无效的账号（避免重复检查）
        self._last_check_time: dict = {}  # 账号最后检查时间
        self._last_refresh_time: dict = {}  # 账号最后刷新时间
        self._check_interval = 60  # 检查间隔（秒）
        self._refresh_interval = 300  # 刷新间隔（秒），同一账号5分钟内不重复刷新

        # 并发刷新相关
        self._max_concurrent = max_concurrent
        self._active_tasks: Dict[str, asyncio.Task] = {}  # 活跃的任务 {task_id: task}
        self._playwright = None
        self._browser = None
        self._code_hub = None  # 共享的验证码中心
        self._captcha_service = None
        self._config_dict = None  # 保存配置用于创建浏览器上下文

        # 并发注册相关
        self._register_queue: asyncio.Queue = None  # 注册队列
        self._queued_emails: Set[str] = set()  # 已在队列中的邮箱（防止重复加入）
        self._registering: Set[str] = set()  # 正在注册的邮箱
        self._register_results: Dict[str, dict] = {}  # 注册结果 {email: result}

        # 初始化锁（防止多个任务同时初始化共享资源）
        self._init_lock = asyncio.Lock()

    async def initialize(self):
        """初始化服务"""
        print(f"[Credential Service] Initializing...")

        if self._initialized:
            print(f"[Credential Service] Already initialized, skip")
            return

        # 初始化刷新队列
        self._refresh_queue = asyncio.Queue()

        # 初始化注册队列
        self._register_queue = asyncio.Queue()

        config = config_manager.config
        auto_login_config = config.auto_login

        print(f"[Credential Service] auto_login config exists: {auto_login_config is not None}")
        if auto_login_config:
            print(f"[Credential Service] auto_login.enabled: {auto_login_config.enabled}")

        # 检查是否配置了自动登录
        if auto_login_config and auto_login_config.enabled:
            try:
                from app.services.auto_login import AutoLoginService
                # 转换为字典格式
                self._config_dict = {
                    "enabled": auto_login_config.enabled,
                    "qq_email": {
                        "address": auto_login_config.qq_email.address,
                        "auth_code": auto_login_config.qq_email.auth_code,
                        "imap_server": auto_login_config.qq_email.imap_server,
                        "imap_port": auto_login_config.qq_email.imap_port,
                    },
                    "verification_timeout": auto_login_config.verification_timeout,
                    "retry_count": auto_login_config.retry_count,
                    # 添加 headless 配置，默认 True
                    "headless": getattr(auto_login_config, 'headless', True),
                    # 添加 YesCaptcha API key（用于绕过 reCAPTCHA）
                    "yescaptcha_api_key": getattr(auto_login_config, 'yescaptcha_api_key', ''),
                    # 添加代理配置
                    "proxy": config.proxy,
                }
                self._auto_login_service = AutoLoginService(self._config_dict)
                # 打印服务状态
                captcha_status = "Configured" if self._config_dict['yescaptcha_api_key'] else "Not configured"
                proxy_status = self._config_dict['proxy'] or "None"

                print(f"\n{'='*60}")
                print(f"[Credential Service] Concurrent Auto-Refresh Initialized")
                print(f"{'='*60}")
                print(f"  - Max Concurrent: {self._max_concurrent}")
                print(f"  - Refresh Cooldown: {self._refresh_interval}s")
                print(f"  - Headless Mode: {self._config_dict['headless']}")
                print(f"  - Proxy: {proxy_status}")
                print(f"  - YesCaptcha: {captcha_status}")
                print(f"  - QQ Email: {auto_login_config.qq_email.address}")
                print(f"{'='*60}\n")

                logger.info(f"Credential auto-refresh service enabled (max_concurrent={self._max_concurrent})")

                # 初始化共享的浏览器和验证码中心（延迟初始化，首次刷新时启动）
                # 这样可以避免服务启动时就占用资源

                # 启动并发后台刷新任务
                self._background_task = asyncio.create_task(self._concurrent_refresh_worker())
                print(f"[Credential Service] Background refresh worker started")
                logger.info("Concurrent credential refresh worker started")

            except ImportError as e:
                print(f"[Credential Service] Warning: auto_login module not installed: {e}")
                logger.warning(f"auto_login module not installed: {e}")
            except Exception as e:
                print(f"[Credential Service] Warning: Failed to initialize auto_login service: {e}")
                logger.warning(f"Failed to initialize auto_login service: {e}")
        else:
            print(f"[Credential Service] auto_login not enabled, skip concurrent refresh initialization")
            logger.info("auto_login not enabled, skip concurrent refresh initialization")

        self._initialized = True

    async def _ensure_shared_resources(self):
        """确保共享资源已初始化（浏览器、验证码中心）"""
        # 快速检查（无锁）
        if self._browser is not None:
            return True

        # 加锁防止多个任务同时初始化
        async with self._init_lock:
            # 再次检查（可能其他任务已经初始化完成）
            if self._browser is not None:
                return True

            if not self._config_dict:
                return False

            try:
                from playwright.async_api import async_playwright
                from app.services.auto_login.concurrent_service import VerificationCodeHub

                # 启动浏览器
                print("[Credential Service] Initializing shared browser...")
                self._playwright = await async_playwright().start()
                
                # 准备浏览器启动参数
                launch_options = {
                    "headless": self._config_dict.get("headless", True),
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                        "--disable-infobars",
                    ]
                }
                
                # 如果配置了代理，添加到启动参数
                proxy_url = self._config_dict.get("proxy")
                if proxy_url:
                    # Chromium 不支持 socks5h:// 协议头，需要替换为 socks5://
                    # 但 socks5:// 在 Chromium 中默认就是远程 DNS 解析
                    browser_proxy_url = proxy_url.replace("socks5h://", "socks5://")
                    launch_options["proxy"] = {"server": browser_proxy_url}
                    print(f"[Credential Service] Using proxy for browser: {browser_proxy_url}")
                
                self._browser = await self._playwright.chromium.launch(**launch_options)

                # 启动验证码中心
                print("[Credential Service] Starting verification code hub...")
                self._code_hub = VerificationCodeHub(self._config_dict.get("qq_email", {}))
                await self._code_hub.start()

                # 初始化打码服务
                yescaptcha_key = self._config_dict.get("yescaptcha_api_key", "")
                if yescaptcha_key:
                    from app.services.auto_login.captcha_service import YesCaptchaService
                    self._captcha_service = YesCaptchaService(yescaptcha_key)

                print(f"[Credential Service] Shared resources initialized (max_concurrent={self._max_concurrent})")
                return True

            except Exception as e:
                logger.error(f"Failed to initialize shared resources: {e}")
                return False

    async def _close_shared_resources(self):
        """关闭共享资源"""
        if self._code_hub:
            await self._code_hub.stop()
            self._code_hub = None

        if self._captcha_service:
            await self._captcha_service.close()
            self._captcha_service = None

        if self._browser:
            await self._browser.close()
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def shutdown(self):
        """关闭服务"""
        # 取消所有活跃的刷新任务
        for account_index, task in list(self._active_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._active_tasks.clear()

        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
            self._background_task = None

        # 关闭共享资源
        await self._close_shared_resources()

        if self._auto_login_service:
            await self._auto_login_service.close()

    async def _concurrent_refresh_worker(self):
        """
        并发工作线程

        维护最多 max_concurrent 个并发任务，
        同时处理刷新和注册任务。
        """
        print(f"[Credential Service] Worker started, waiting for tasks...")
        logger.info(f"Concurrent credential worker started")

        idle_cycles = 0  # 空闲循环计数

        while True:
            try:
                # 清理已完成的任务
                completed_refresh = 0
                completed_register = 0
                for task_id, task in list(self._active_tasks.items()):
                    if task.done():
                        # 获取任务结果
                        try:
                            success, error = task.result()

                            if task_id.startswith("refresh_"):
                                account_index = int(task_id.split("_")[1])
                                account = config_manager.get_account(account_index)
                                account_note = account.note if account else f"Account{account_index}"

                                if success:
                                    logger.info(f"[Refresh] {account_note} success")
                                    self._invalid_accounts.discard(account_index)
                                    try:
                                        from app.services.account_manager import account_manager
                                        account_manager.clear_account_cooldown(account_index)
                                    except:
                                        pass
                                else:
                                    logger.warning(f"[Refresh] {account_note} failed: {error}")
                                    # 记录刷新失败到账号池服务
                                    try:
                                        from app.services.account_pool_service import account_pool_service
                                        account_pool_service.record_refresh_failure(account_note)
                                    except:
                                        pass

                                self._refreshing.discard(account_index)
                                completed_refresh += 1

                            elif task_id.startswith("register_"):
                                email = task_id.split("_", 1)[1]
                                self._registering.discard(email)

                                if success:
                                    logger.info(f"[Register] {email} success")
                                    self._register_results[email] = {"success": True, "error": None}
                                else:
                                    logger.warning(f"[Register] {email} failed: {error}")
                                    self._register_results[email] = {"success": False, "error": error}

                                completed_register += 1

                        except asyncio.CancelledError:
                            pass
                        except Exception as e:
                            logger.error(f"Task {task_id} error: {e}")

                        del self._active_tasks[task_id]

                # 如果有空闲槽位，优先从注册队列取任务，再从刷新队列取
                started_refresh = 0
                started_register = 0

                while len(self._active_tasks) < self._max_concurrent:
                    # 优先处理注册任务
                    try:
                        email = self._register_queue.get_nowait()

                        if email in self._registering:
                            self._queued_emails.discard(email)
                            continue

                        # 先加入 registering，再从 queued 移除，避免竞态条件
                        self._registering.add(email)
                        self._queued_emails.discard(email)

                        task_id = f"register_{email}"
                        task = asyncio.create_task(
                            self._do_concurrent_register(email)
                        )
                        self._active_tasks[task_id] = task
                        started_register += 1
                        continue
                    except asyncio.QueueEmpty:
                        pass

                    # 再处理刷新任务
                    try:
                        account_index = self._refresh_queue.get_nowait()

                        if account_index in self._refreshing:
                            self._queued_accounts.discard(account_index)
                            continue

                        now = time.time()
                        last_refresh = self._last_refresh_time.get(account_index, 0)
                        if now - last_refresh < self._refresh_interval:
                            self._queued_accounts.discard(account_index)
                            continue

                        account = config_manager.get_account(account_index)
                        if not account:
                            self._queued_accounts.discard(account_index)
                            continue

                        # 先加入 refreshing，再从 queued 移除
                        self._refreshing.add(account_index)
                        self._queued_accounts.discard(account_index)
                        self._last_refresh_time[account_index] = now

                        task_id = f"refresh_{account_index}"
                        task = asyncio.create_task(
                            self._do_concurrent_refresh(account_index, account)
                        )
                        self._active_tasks[task_id] = task
                        started_refresh += 1
                    except asyncio.QueueEmpty:
                        break

                # 如果有任务变化，打印状态
                if started_refresh > 0 or started_register > 0 or completed_refresh > 0 or completed_register > 0:
                    idle_cycles = 0
                    self._print_concurrent_status(
                        started_refresh, started_register,
                        completed_refresh, completed_register
                    )

                # 如果没有活跃任务，等待队列
                if not self._active_tasks:
                    # 同时监听两个队列
                    try:
                        done, pending = await asyncio.wait(
                            [
                                asyncio.create_task(self._refresh_queue.get()),
                                asyncio.create_task(self._register_queue.get())
                            ],
                            timeout=30.0,
                            return_when=asyncio.FIRST_COMPLETED
                        )
                        # 将取出的任务放回队列
                        for task in done:
                            try:
                                item = task.result()
                                if isinstance(item, int):
                                    await self._refresh_queue.put(item)
                                else:
                                    await self._register_queue.put(item)
                            except:
                                pass
                        # 取消未完成的任务
                        for task in pending:
                            task.cancel()
                        idle_cycles = 0
                    except asyncio.TimeoutError:
                        idle_cycles += 1
                        if idle_cycles >= 60 and self._browser is not None:
                            print(f"[Credential Service] Idle timeout, closing shared resources")
                            await self._close_shared_resources()
                else:
                    await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                print(f"[Concurrent Worker] Stopped")
                logger.info("Concurrent credential worker stopped")
                break
            except Exception as e:
                logger.error(f"Concurrent worker error: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    def _print_concurrent_status(self, started_refresh: int, started_register: int,
                                  completed_refresh: int, completed_register: int):
        """打印并发状态"""
        active = len(self._active_tasks)
        queued_refresh = self._refresh_queue.qsize()
        queued_register = self._register_queue.qsize()

        active_items = []
        for task_id in self._active_tasks.keys():
            if task_id.startswith("refresh_"):
                account_index = int(task_id.split("_")[1])
                acc = config_manager.get_account(account_index)
                active_items.append(acc.note if acc else f"Acc{account_index}")
            elif task_id.startswith("register_"):
                email = task_id.split("_", 1)[1]
                active_items.append(email.split("@")[0])

        print(f"\n{'='*60}")
        print(f"[Concurrent Worker] Status Update")
        print(f"{'='*60}")
        print(f"  Active: {active}/{self._max_concurrent} | Queued: {queued_refresh} refresh, {queued_register} register")
        print(f"  Started: +{started_refresh} refresh, +{started_register} register")
        print(f"  Completed: +{completed_refresh} refresh, +{completed_register} register")
        if active_items:
            print(f"  Running: {', '.join(active_items)}")
        print(f"{'='*60}\n")

    async def _do_concurrent_refresh(
        self,
        account_index: int,
        account: 'AccountConfig'
    ) -> Tuple[bool, str]:
        """
        执行单个账号的并发刷新

        使用共享的浏览器和验证码中心
        完整实现参考 service.py:refresh_account
        """
        from app.services.auto_login.service import (
            _safe_goto, _handle_trial_signup_page,
            _dismiss_welcome_dialog, inject_stealth_scripts
        )
        from app.services.auto_login.human_behavior import HumanBehavior
        from app.services.auto_login.captcha_service import CaptchaInterceptor
        from urllib.parse import urlparse, parse_qs
        import re
        import random

        context = None
        page = None
        tag = f"[{account.note}]"  # 简短标签

        try:
            # 确保共享资源已初始化
            if not await self._ensure_shared_resources():
                return False, "Failed to init shared resources"

            # 获取 Google 邮箱
            google_email = self._get_google_email_for_account(account)
            if not google_email:
                return False, f"Email not found for {account.note}"

            print(f"{tag} Starting refresh...")

            # 创建独立的浏览器上下文
            context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )

            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                window.chrome = { runtime: {} };
            """)

            page = await context.new_page()
            await inject_stealth_scripts(page)

            human = HumanBehavior(page)

            # 访问目标页面
            target_url = f"https://business.gemini.google/home/cid/{account.team_id}"
            if account.csesidx:
                target_url += f"?csesidx={account.csesidx}"

            print(f"{tag} Visiting: {target_url[:60]}...")
            await _safe_goto(page, target_url, wait_until="networkidle", timeout=60000)

            # 模拟人类行为预热
            await human.warm_up_session(duration=random.uniform(3, 5))

            current_url = page.url
            print(f"{tag} Current URL: {current_url[:60]}...")

            # 首先检查是否在错误页面
            if await self._is_error_page(page):
                print(f"{tag} Error page detected on entry...")
                await self._handle_error_page(page, human)
                current_url = page.url

            # 首先检查是否在首次注册页面
            if "admin/create" in current_url:
                print(f"{tag} Trial signup page detected...")
                display_name = account.note or google_email.split("@")[0]
                if not await _handle_trial_signup_page(page, display_name):
                    return False, "Trial signup failed"
                await asyncio.sleep(3)
                current_url = page.url

            # 检查是否需要登录
            need_login = (
                "accounts.google.com" in current_url or
                "auth.business.gemini.google" in current_url or
                "signin" in current_url.lower()
            )

            if need_login:
                print(f"{tag} Login required...")
                login_success = await self._concurrent_login(page, human, google_email, tag)

                if not login_success:
                    return False, "Login failed"

                # 重新访问目标页面
                print(f"{tag} Re-visiting target page...")
                await _safe_goto(page, target_url, wait_until="networkidle", timeout=60000)
                await asyncio.sleep(3)
                current_url = page.url
                print(f"{tag} After login URL: {current_url[:60]}...")

            # 处理首次注册（登录后可能出现）
            if "admin/create" in current_url:
                print(f"{tag} Trial signup page (after login)...")
                display_name = account.note or google_email.split("@")[0]
                if not await _handle_trial_signup_page(page, display_name):
                    return False, "Trial signup failed"
                await asyncio.sleep(3)
                current_url = page.url

            # 等待进入聊天页面（最多60秒）
            print(f"{tag} Waiting for chat page (max 60s)...")
            trial_signup_handled = False  # 标记是否已处理过注册页面
            for i in range(60):
                current_url = page.url

                if "/cid/" in current_url:
                    print(f"{tag} Entered chat page!")
                    break

                # 检查是否出现注册页面（只处理一次）
                if not trial_signup_handled and "admin/create" in current_url:
                    print(f"{tag} Trial signup page during wait...")
                    display_name = account.note or google_email.split("@")[0]
                    if await _handle_trial_signup_page(page, display_name):
                        trial_signup_handled = True
                        await asyncio.sleep(3)
                        continue

                # 检查错误页面
                if await self._is_error_page(page):
                    print(f"{tag} Error page during wait...")
                    await self._handle_error_page(page, human)

                if i > 0 and i % 10 == 0:
                    print(f"{tag} Still waiting... [{i}/60] URL={current_url[:50]}...")

                await asyncio.sleep(1)

            # 关闭欢迎弹窗
            await _dismiss_welcome_dialog(page)

            # 提取凭证
            current_url = page.url
            print(f"{tag} Final URL: {current_url[:60]}...")

            if "business.gemini.google" not in current_url or "/cid/" not in current_url:
                return False, f"Failed to enter chat page: {current_url[:50]}"

            credentials = {}

            cid_match = re.search(r'/cid/([^/?#]+)', current_url)
            if cid_match:
                credentials["team_id"] = cid_match.group(1)
                print(f"{tag} team_id: {credentials['team_id'][:20]}...")

            parsed = urlparse(current_url)
            params = parse_qs(parsed.query)
            if "csesidx" in params:
                credentials["csesidx"] = params["csesidx"][0]
                print(f"{tag} csesidx: {credentials['csesidx'][:20]}...")

            cookies = await context.cookies("https://business.gemini.google")
            for cookie in cookies:
                if cookie["name"] == "__Secure-C_SES":
                    credentials["secure_c_ses"] = cookie["value"]
                    print(f"{tag} secure_c_ses: {cookie['value'][:20]}...")
                elif cookie["name"] == "__Host-C_OSES":
                    credentials["host_c_oses"] = cookie["value"]
                    print(f"{tag} host_c_oses: {cookie['value'][:20]}...")

            if credentials.get("secure_c_ses"):
                credentials["refresh_time"] = datetime.now().isoformat()
                self._update_account_credentials(account_index, credentials)
                print(f"{tag} SUCCESS - Credentials extracted!")
                return True, ""
            else:
                return False, "Failed to get secure_c_ses cookie"

        except Exception as e:
            logger.error(f"Refresh error [{account.note}]: {e}")
            import traceback
            traceback.print_exc()
            return False, str(e)

        finally:
            self._refreshing.discard(account_index)
            if page:
                try:
                    await page.close()
                except:
                    pass
            if context:
                try:
                    await context.close()
                except:
                    pass

    async def _is_verification_page(self, page) -> bool:
        """
        检查是否在验证码页面（同时检查 URL 和页面内容）
        """
        try:
            current_url = page.url

            # 检查 URL 指示器
            verification_url_indicators = [
                "accountverification.business.gemini.google",
                "accounts.google.com/v2/challenge",
            ]
            for indicator in verification_url_indicators:
                if indicator in current_url:
                    return True

            # 检查页面内容关键词
            page_content = await page.content()
            verification_keywords = [
                "请输入验证码",
                "输入验证码",
                "verification",
                "verify",
                "enter the code",
                "security code",
                "验证码",
            ]
            for keyword in verification_keywords:
                if keyword.lower() in page_content.lower():
                    return True

            return False
        except:
            return False

    async def _is_error_page(self, page) -> bool:
        """
        检查是否在错误页面（被检测到自动化）
        """
        try:
            current_url = page.url
            page_content = await page.content()

            error_indicators = [
                "signin-error",
                "请试试其他方法",
                "Try another way",
                "Something went wrong",
            ]

            for indicator in error_indicators:
                if indicator in current_url or indicator in page_content:
                    print(f"  [!] 检测到错误页面: {indicator}")
                    return True

            return False
        except:
            return False

    async def _is_page_loading(self, page) -> bool:
        """
        检测页面是否正在加载
        """
        try:
            # 检测常见的加载指示器
            loading_selectors = [
                '[role="progressbar"]',
                '.loading',
                '.spinner',
                '[aria-busy="true"]',
                'mat-spinner',
                'mat-progress-spinner',
            ]

            for selector in loading_selectors:
                try:
                    elem = await page.query_selector(selector)
                    if elem and await elem.is_visible():
                        return True
                except:
                    continue

            # 检测按钮是否被禁用（表示正在处理）
            disabled_button = await page.query_selector('button[disabled]')
            if disabled_button and await disabled_button.is_visible():
                return True

            return False
        except:
            return False

    async def _handle_error_page(self, page, human) -> bool:
        """
        处理错误页面（点击返回按钮重试）
        """
        try:
            print("  [错误] 尝试从错误页面恢复...")

            retry_selectors = [
                'button:has-text("注册或登录")',
                'button:has-text("Sign in")',
                'button:has-text("登录")',
                'a:has-text("注册或登录")',
                'a:has-text("Sign in")',
            ]

            for selector in retry_selectors:
                try:
                    btn = await page.query_selector(selector)
                    if btn and await btn.is_visible():
                        await human.wait_random(1000, 2000)
                        await human.human_click(btn)
                        print(f"  [错误] 已点击返回按钮")
                        await human.wait_random(2000, 4000)
                        return True
                except:
                    continue

            print("  [错误] 未找到返回按钮，尝试返回上一页...")
            await page.go_back()
            await asyncio.sleep(3)
            return True

        except Exception as e:
            print(f"  [错误] 处理错误页面失败: {e}")
            return False

    async def _wait_for_code_sent(self, page, timeout: int = 60) -> bool:
        """
        等待验证码发送完成的提示出现
        """
        import time as time_module

        sent_indicators = [
            "验证码已发送",
            "请查收你的邮件",
            "请查收您的邮件",
            "已发送验证码",
            "代码已发送",
            "Code sent",
            "code has been sent",
            "Check your email",
            "check your inbox",
        ]

        start_time = time_module.time()

        while time_module.time() - start_time < timeout:
            try:
                # 检查是否还在验证码页面（如果跳转了就停止等待）
                if not await self._is_verification_page(page):
                    print(f"  页面已跳转，停止等待")
                    return False

                page_content = await page.content()

                for indicator in sent_indicators:
                    if indicator.lower() in page_content.lower():
                        print(f"  检测到: {indicator}")
                        return True

                # 检查 toast/snackbar 消息
                toast_selectors = [
                    '.mat-snack-bar-container',
                    '.snackbar',
                    '.toast',
                    '[role="alert"]',
                    '.mdc-snackbar',
                    '.notification',  # 添加 notification 选择器
                ]

                for selector in toast_selectors:
                    try:
                        elem = await page.query_selector(selector)
                        if elem and await elem.is_visible():
                            text = await elem.text_content()
                            if text:
                                for indicator in sent_indicators:
                                    if indicator.lower() in text.lower():
                                        print(f"  检测到消息: {indicator}")
                                        return True
                    except:
                        continue

            except:
                pass

            await asyncio.sleep(0.5)

        print(f"  等待发送提示超时 ({timeout}秒)，尝试继续...")
        return False

    async def _concurrent_login(
        self,
        page,
        human: 'HumanBehavior',
        google_email: str,
        tag: str = "",
        max_retries: int = 3
    ) -> bool:
        """
        并发友好的登录流程（带重试机制）
        """
        from app.services.auto_login.captcha_service import CaptchaInterceptor

        for retry in range(max_retries):
            if retry > 0:
                wait_time = (retry + 1) * 5
                print(f"{tag} Retry {retry + 1}/{max_retries}, waiting {wait_time}s...")
                await asyncio.sleep(wait_time)

            try:
                result = await self._do_concurrent_login_once(
                    page, human, google_email, tag
                )
                if result:
                    return True

                # 检查是否因为错误页面失败
                if await self._is_error_page(page):
                    print(f"{tag} Error page detected, retrying...")
                    await self._handle_error_page(page, human)
                    continue
                else:
                    print(f"{tag} Login failed, retrying...")
                    continue

            except Exception as e:
                logger.error(f"Login error [{google_email}]: {e}")
                if retry < max_retries - 1:
                    continue
                return False

        print(f"{tag} Max retries ({max_retries}) reached, login failed")
        return False

    async def _do_concurrent_login_once(
        self,
        page,
        human: 'HumanBehavior',
        google_email: str,
        tag: str = ""
    ) -> bool:
        """
        执行单次登录尝试
        """
        from app.services.auto_login.captcha_service import CaptchaInterceptor
        import random

        try:
            current_url = page.url

            # 首先检查是否在错误页面
            if await self._is_error_page(page):
                print(f"{tag} Error page detected...")
                await self._handle_error_page(page, human)
                current_url = page.url

            # 输入邮箱
            if "auth.business.gemini.google" in current_url:
                # 随机等待（模拟人类阅读页面）
                await human.wait_random(1000, 2500)

                # 等待输入框出现
                try:
                    await page.wait_for_selector('#email-input', timeout=30000)
                except:
                    print(f"{tag} Email input not found")
                    return False

                email_input = await page.query_selector('#email-input')
                if email_input:
                    # 随机鼠标移动
                    await human.random_mouse_movement(random.randint(1, 3))

                    # 人类打字模拟
                    await human.type_like_human(email_input, google_email, speed="human")
                    await human.wait_random(300, 800)

                    login_btn = await page.query_selector('#log-in-button')
                    if login_btn:
                        await human.human_click(login_btn)
                    else:
                        await page.keyboard.press("Enter")

            elif "accounts.google.com" in current_url:
                # 随机等待（模拟人类阅读页面）
                await human.wait_random(1000, 2500)

                # 等待输入框出现
                try:
                    await page.wait_for_selector('input[type="email"]', timeout=30000)
                except:
                    print(f"{tag} Email input not found")
                    return False

                email_input = await page.query_selector('input[type="email"]')
                if email_input:
                    # 随机鼠标移动
                    await human.random_mouse_movement(random.randint(1, 3))

                    # 人类打字模拟
                    await human.type_like_human(email_input, google_email, speed="human")
                    await human.wait_random(300, 800)

                    # 点击下一步按钮
                    next_btn = await page.query_selector('#identifierNext')
                    if next_btn:
                        await human.human_click(next_btn)
                    else:
                        await page.keyboard.press("Enter")

            print(f"{tag} Email entered, waiting for page transition...")
            await human.wait_random(3000, 5000)
            current_url = page.url

            # 检查是否出现错误页面
            if await self._is_error_page(page):
                print(f"{tag} Error page after email entry...")
                return False

            # 检查是否需要验证码（使用内容检测）
            for i in range(15):
                current_url = page.url

                # 检查错误页面
                if await self._is_error_page(page):
                    print(f"{tag} Error page detected during wait...")
                    return False

                # 检查是否已登录成功
                if "business.gemini.google/home" in current_url and "auth" not in current_url:
                    print(f"{tag} No verification needed")
                    return True

                # 检查是否在注册页面
                if "admin/create" in current_url:
                    print(f"{tag} Trial signup page detected")
                    return True

                # 检查是否在验证码页面（同时检查 URL 和内容）
                if await self._is_verification_page(page):
                    print(f"{tag} Verification page detected")
                    break

                await asyncio.sleep(1)

            if not await self._is_verification_page(page):
                print(f"{tag} Not on verification page, returning false")
                return False

            # 启动验证码拦截
            interceptor = None
            if self._captcha_service:
                interceptor = CaptchaInterceptor(page, self._captcha_service)
                await interceptor.start_monitoring()

            try:
                # 等待验证码发送成功提示
                print(f"{tag} Waiting for code sent confirmation...")
                sent_detected = await self._wait_for_code_sent(page, timeout=30)
                if sent_detected:
                    print(f"{tag} Code sent confirmation detected!")
                else:
                    print(f"{tag} No confirmation detected, proceeding anyway...")

                request_time = datetime.now()
                print(f"{tag} Waiting for verification code...")

                # 从共享验证码中心获取验证码
                code = await self._code_hub.wait_for_code(
                    target_email=google_email,
                    timeout=self._config_dict.get("verification_timeout", 120),
                    since_time=request_time
                )

                if not code:
                    print(f"{tag} FAILED - No code received")
                    return False

                print(f"{tag} Code received: {code}")

                # 输入验证码（区分 Gemini 和 Google 页面）
                current_url = page.url
                if "accountverification.business.gemini.google" in current_url:
                    # Gemini 验证码页面（6个独立输入框）
                    success = await self._enter_verification_code_gemini(page, code, tag)
                else:
                    # Google 标准验证码页面
                    success = await self._enter_verification_code_google(page, code, tag)

                if not success:
                    print(f"{tag} Failed to enter verification code")
                    return False

                print(f"{tag} Code submitted, verifying...")
                await asyncio.sleep(3)

                # 检查是否成功
                for _ in range(30):
                    current_url = page.url
                    if "business.gemini.google" in current_url and "auth" not in current_url:
                        print(f"{tag} Login successful")
                        return True
                    if "admin/create" in current_url:
                        print(f"{tag} Login successful (new account)")
                        return True
                    await asyncio.sleep(1)

                return False

            finally:
                if interceptor:
                    interceptor.stop_monitoring()

        except Exception as e:
            logger.error(f"Login once error [{google_email}]: {e}")
            return False

    async def _enter_verification_code_gemini(self, page, code: str, tag: str = "") -> bool:
        """
        在 Gemini Business 验证码页面输入验证码（6个独立输入框）
        """
        try:
            print(f"{tag} Entering code on Gemini verification page: {code}")

            # 获取所有文本输入框
            inputs = await page.query_selector_all('input[type="text"]')
            visible_inputs = []
            for inp in inputs:
                if await inp.is_visible():
                    visible_inputs.append(inp)

            print(f"{tag} Found {len(visible_inputs)} input fields")

            if len(visible_inputs) >= 6:
                # 逐个输入每个字符
                for i, char in enumerate(code[:6]):
                    await visible_inputs[i].fill(char)
                    await asyncio.sleep(0.1)
            elif len(visible_inputs) == 1:
                # 只有一个输入框，直接输入整个验证码
                await visible_inputs[0].fill(code)
            else:
                print(f"{tag} Unexpected input count: {len(visible_inputs)}")
                return False

            await asyncio.sleep(0.5)

            # 点击验证按钮
            verify_button = await page.query_selector('button:has-text("验证")')
            if not verify_button:
                verify_button = await page.query_selector('button[type="submit"]')

            if verify_button and await verify_button.is_visible():
                await verify_button.click()
                print(f"{tag} Clicked verify button")
            else:
                # 尝试按回车
                if visible_inputs:
                    await visible_inputs[-1].press("Enter")
                    print(f"{tag} Pressed Enter")

            return True

        except Exception as e:
            print(f"{tag} Gemini code entry error: {e}")
            return False

    async def _enter_verification_code_google(self, page, code: str, tag: str = "") -> bool:
        """
        在 Google 标准验证码页面输入验证码
        """
        try:
            print(f"{tag} Entering code on Google verification page: {code}")

            # 尝试多个可能的输入框选择器
            input_selectors = [
                'input[name="code"]',
                'input[type="tel"]',
                'input[autocomplete="one-time-code"]',
                'input[name="totpPin"]',
                'input[name="Pin"]',
                'input#code',
                'input[type="text"]',
            ]

            for selector in input_selectors:
                try:
                    input_elem = await page.query_selector(selector)
                    if input_elem and await input_elem.is_visible():
                        await input_elem.fill("")
                        await asyncio.sleep(0.3)
                        await input_elem.fill(code)
                        await asyncio.sleep(0.5)
                        print(f"{tag} Code entered using selector: {selector}")

                        # 尝试找到并点击下一步/验证按钮
                        button_selectors = [
                            '#idvPreregisteredPhoneNext',
                            'button[type="submit"]',
                            'button:has-text("Next")',
                            'button:has-text("下一步")',
                            'button:has-text("验证")',
                            'div[role="button"]:has-text("Next")',
                            'div[role="button"]:has-text("下一步")',
                        ]

                        for btn_selector in button_selectors:
                            try:
                                btn = await page.query_selector(btn_selector)
                                if btn and await btn.is_visible():
                                    await btn.click()
                                    print(f"{tag} Clicked button: {btn_selector}")
                                    return True
                            except:
                                continue

                        # 尝试按回车
                        await input_elem.press("Enter")
                        print(f"{tag} Pressed Enter")
                        return True
                except:
                    continue

            print(f"{tag} No verification input found")
            return False

        except Exception as e:
            print(f"{tag} Google code entry error: {e}")
            return False

    async def _do_concurrent_register(self, email: str) -> Tuple[bool, str]:
        """
        执行单个邮箱的并发注册

        使用共享的浏览器和验证码中心
        完整实现参考 service.py:register_new_account
        """
        from app.services.auto_login.service import (
            _safe_goto, _handle_trial_signup_page,
            _dismiss_welcome_dialog, inject_stealth_scripts
        )
        from app.services.auto_login.human_behavior import HumanBehavior
        from app.services.auto_login.captcha_service import CaptchaInterceptor
        from urllib.parse import urlparse, parse_qs
        import re
        import random

        context = None
        page = None
        email_prefix = email.split("@")[0]
        tag = f"[{email_prefix}]"

        try:
            # 确保共享资源已初始化
            if not await self._ensure_shared_resources():
                return False, "Failed to init shared resources"

            print(f"{tag} Starting registration...")

            # 创建独立的浏览器上下文
            context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )

            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                window.chrome = { runtime: {} };
            """)

            page = await context.new_page()
            await inject_stealth_scripts(page)

            human = HumanBehavior(page)

            # 访问 Gemini Business 入口页面
            target_url = "https://business.gemini.google/"
            print(f"{tag} Visiting: {target_url}")
            await _safe_goto(page, target_url, wait_until="networkidle", timeout=60000)

            # 模拟人类行为预热
            await human.warm_up_session(duration=random.uniform(3, 5))

            current_url = page.url
            print(f"{tag} Current URL: {current_url[:60]}...")

            # 首先检查是否在错误页面
            if await self._is_error_page(page):
                print(f"{tag} Error page detected on entry...")
                await self._handle_error_page(page, human)
                current_url = page.url

            # 首先检查是否在首次注册页面（可能已有会话）
            if "admin/create" in current_url:
                print(f"{tag} Trial signup page detected (existing session)...")
                display_name = email_prefix
                if not await _handle_trial_signup_page(page, display_name):
                    return False, "Trial signup failed"
                await asyncio.sleep(3)
                current_url = page.url

            # 检查是否需要登录
            need_login = (
                "accounts.google.com" in current_url or
                "auth.business.gemini.google" in current_url or
                "signin" in current_url.lower()
            )

            print(f"{tag} Need login: {need_login}")

            if need_login:
                print(f"{tag} Starting login flow...")
                login_success = await self._concurrent_login(page, human, email, tag)

                if not login_success:
                    return False, "Login failed"

                await asyncio.sleep(3)
                current_url = page.url
                print(f"{tag} After login URL: {current_url[:60]}...")

            # 处理首次注册页面（登录后可能出现）
            if "admin/create" in current_url:
                print(f"{tag} Trial signup page detected (after login)...")
                display_name = email_prefix
                if not await _handle_trial_signup_page(page, display_name):
                    return False, "Trial signup failed"
                await asyncio.sleep(3)
                current_url = page.url

            # 等待进入聊天页面（最多60秒，与非并发版本一致）
            print(f"{tag} Waiting for chat page (max 60s)...")
            trial_signup_handled = False  # 标记是否已处理过注册页面
            for i in range(60):
                current_url = page.url

                # 检查是否已进入聊天页面
                if "/cid/" in current_url or "/home/cid/" in current_url:
                    print(f"{tag} Entered chat page!")
                    break

                # 检查是否出现注册页面（只处理一次）
                if not trial_signup_handled and "admin/create" in current_url:
                    print(f"{tag} Trial signup page appeared during wait...")
                    display_name = email_prefix
                    if await _handle_trial_signup_page(page, display_name):
                        trial_signup_handled = True
                        await asyncio.sleep(3)
                        continue

                # 检查错误页面
                if await self._is_error_page(page):
                    print(f"{tag} Error page during wait...")
                    await self._handle_error_page(page, human)

                if i > 0 and i % 10 == 0:
                    print(f"{tag} Still waiting... [{i}/60] URL={current_url[:50]}...")

                await asyncio.sleep(1)

            # 关闭欢迎弹窗
            print(f"{tag} Closing welcome dialog...")
            await _dismiss_welcome_dialog(page)

            # 提取凭证
            current_url = page.url
            print(f"{tag} Final URL: {current_url[:60]}...")

            if "business.gemini.google" not in current_url:
                return False, f"Failed to enter Gemini Business: {current_url[:50]}"

            credentials = {"note": email_prefix, "google_email": email}

            # 提取 team_id
            cid_match = re.search(r'/cid/([^/?#]+)', current_url)
            if cid_match:
                credentials["team_id"] = cid_match.group(1)
                print(f"{tag} team_id: {credentials['team_id'][:20]}...")

            # 提取 csesidx
            parsed = urlparse(current_url)
            params = parse_qs(parsed.query)
            if "csesidx" in params:
                credentials["csesidx"] = params["csesidx"][0]
                print(f"{tag} csesidx: {credentials['csesidx'][:20]}...")

            # 提取 cookies
            cookies = await context.cookies("https://business.gemini.google")
            for cookie in cookies:
                if cookie["name"] == "__Secure-C_SES":
                    credentials["secure_c_ses"] = cookie["value"]
                    print(f"{tag} secure_c_ses: {cookie['value'][:20]}...")
                elif cookie["name"] == "__Host-C_OSES":
                    credentials["host_c_oses"] = cookie["value"]
                    print(f"{tag} host_c_oses: {cookie['value'][:20]}...")

            if credentials.get("secure_c_ses") and credentials.get("team_id"):
                credentials["refresh_time"] = datetime.now().isoformat()
                # 添加账号到配置
                self._add_account_to_config(credentials)
                print(f"{tag} SUCCESS - Account registered!")
                return True, ""
            else:
                missing = []
                if not credentials.get("secure_c_ses"):
                    missing.append("secure_c_ses")
                if not credentials.get("team_id"):
                    missing.append("team_id")
                return False, f"Missing credentials: {', '.join(missing)}"

        except Exception as e:
            logger.error(f"Register error [{email}]: {e}")
            import traceback
            traceback.print_exc()
            return False, str(e)

        finally:
            self._registering.discard(email)
            if page:
                try:
                    await page.close()
                except:
                    pass
            if context:
                try:
                    await context.close()
                except:
                    pass

    async def queue_register(self, email: str) -> bool:
        """
        将邮箱加入注册队列（非阻塞）

        Args:
            email: 邮箱地址

        Returns:
            True 如果成功加入队列
        """
        if not self._initialized or not self._register_queue:
            logger.warning(f"Service not initialized, {email} cannot be queued")
            return False

        if not self._config_dict:
            logger.warning(f"auto_login not configured, {email} cannot be queued")
            return False

        if email in self._registering:
            logger.debug(f"{email} already registering")
            return False

        if email in self._queued_emails:
            logger.debug(f"{email} already in queue")
            return False

        # 检查是否已经注册过
        account_index, existing = self._find_account_by_email(email)
        if existing:
            logger.debug(f"{email} already registered")
            return False

        try:
            self._queued_emails.add(email)
            self._register_queue.put_nowait(email)
            logger.info(f"{email} queued for registration")
            return True
        except asyncio.QueueFull:
            self._queued_emails.discard(email)
            logger.warning(f"Register queue full, {email} cannot be added")
            return False

    async def wait_for_registrations(self, emails: List[str], timeout: float = 600) -> Dict[str, dict]:
        """
        等待一批邮箱注册完成

        Args:
            emails: 邮箱列表
            timeout: 超时时间（秒）

        Returns:
            注册结果 {email: {"success": bool, "error": str or None}}
        """
        start_time = time.time()

        # 先加入队列
        for email in emails:
            await self.queue_register(email)

        # 等待所有邮箱处理完成
        results = {}
        while time.time() - start_time < timeout:
            all_done = True
            for email in emails:
                if email in self._register_results:
                    results[email] = self._register_results[email]
                elif email in self._registering or email in self._queued_emails:
                    all_done = False
                elif f"register_{email}" in self._active_tasks:
                    # 任务正在运行中
                    all_done = False
                else:
                    # 检查结果是否已经存在
                    if email in self._register_results:
                        results[email] = self._register_results[email]
                    else:
                        # 可能还没开始处理，等一下
                        all_done = False

            if all_done and len(results) == len(emails):
                break

            await asyncio.sleep(1)

        # 超时后收集剩余结果
        for email in emails:
            if email not in results:
                if email in self._register_results:
                    results[email] = self._register_results[email]
                else:
                    results[email] = {"success": False, "error": "Timeout"}

        return results

    async def _background_refresh_worker(self):
        """后台刷新工作线程（已废弃，使用 _concurrent_refresh_worker）"""
        # 保留此方法以兼容旧代码，但实际使用并发版本
        await self._concurrent_refresh_worker()

    async def check_credential(self, account_index: int) -> Tuple[bool, str]:
        """
        检查指定账号的凭证是否有效

        Args:
            account_index: 账号索引

        Returns:
            (is_valid, error_message)
        """
        account = config_manager.get_account(account_index)
        if not account:
            return False, f"账号 {account_index} 不存在"

        proxy = config_manager.config.proxy or None
        return await verify_credential(account, proxy)

    async def refresh_credential(self, account_index: int) -> Tuple[bool, str]:
        """
        刷新指定账号的凭证

        Args:
            account_index: 账号索引

        Returns:
            (success, error_message)
        """
        async with self._lock:
            # 检查是否已经在刷新
            if account_index in self._refreshing:
                return False, "账号正在刷新中"

            self._refreshing.add(account_index)

        try:
            account = config_manager.get_account(account_index)
            if not account:
                return False, f"账号 {account_index} 不存在"

            if not self._auto_login_service:
                return False, "自动登录服务未启用"

            # 获取账号对应的 Google 邮箱
            google_email = self._get_google_email_for_account(account)
            if not google_email:
                return False, f"未找到账号 {account.note} 对应的 Google 邮箱"

            print(f"[凭证刷新] 开始刷新账号 {account_index} ({account.note})")
            print(f"[凭证刷新] Google 邮箱: {google_email}")
            logger.info(f"开始刷新账号 {account_index} ({account.note}) 的凭证")

            # 调用自动登录服务刷新凭证
            new_credentials = await self._auto_login_service.refresh_account(
                account=account,
                google_email=google_email
            )

            if new_credentials:
                # 更新配置
                self._update_account_credentials(account_index, new_credentials)
                print(f"[凭证刷新] 账号 {account_index} ({account.note}) 凭证刷新成功")
                logger.info(f"账号 {account_index} ({account.note}) 凭证刷新成功")
                return True, ""
            else:
                print(f"[凭证刷新] 账号 {account_index} ({account.note}) 自动登录刷新失败")
                # 记录刷新失败
                try:
                    from app.services.account_pool_service import account_pool_service
                    account_pool_service.record_refresh_failure(account.note)
                except:
                    pass
                return False, "自动登录刷新失败"

        except Exception as e:
            print(f"[凭证刷新] 账号 {account_index} 刷新出错: {e}")
            logger.error(f"刷新账号 {account_index} 凭证时出错: {e}")
            # 记录刷新失败
            try:
                from app.services.account_pool_service import account_pool_service
                account_pool_service.record_refresh_failure(account.note)
            except:
                pass
            return False, str(e)
        finally:
            async with self._lock:
                self._refreshing.discard(account_index)

    def _get_google_email_for_account(self, account: AccountConfig) -> Optional[str]:
        """
        获取账号对应的 Google 邮箱

        从 credient.txt 中查找匹配的邮箱（精确匹配）
        """
        credentials_file = Path(__file__).parent.parent.parent / "credient.txt"
        if not credentials_file.exists():
            print(f"  [!] credient.txt 不存在: {credentials_file}")
            return None

        try:
            with open(credentials_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            emails = [line.strip() for line in lines if line.strip() and not line.startswith("#") and "@" in line]

            # 通过 note 字段精确匹配邮箱前缀
            account_note = account.note.lower() if account.note else ""
            for email in emails:
                email_prefix = email.split("@")[0].lower()
                # 精确匹配：邮箱前缀必须完全等于 note
                if email_prefix == account_note:
                    return email

            print(f"  [!] 未在 credient.txt 中找到账号 {account.note} 对应的邮箱")
            return None
        except Exception as e:
            print(f"  [!] 读取 credient.txt 失败: {e}")
            logger.error(f"读取 credient.txt 失败: {e}")
            return None

    def _update_account_credentials(self, account_index: int, credentials: dict):
        """更新账号凭证到配置文件"""
        account = config_manager.get_account(account_index)
        if not account:
            return

        # 更新凭证字段
        if credentials.get("secure_c_ses"):
            account.secure_c_ses = credentials["secure_c_ses"]
        if credentials.get("host_c_oses"):
            account.host_c_oses = credentials["host_c_oses"]
        if credentials.get("team_id"):
            account.team_id = credentials["team_id"]
        if credentials.get("csesidx"):
            account.csesidx = credentials["csesidx"]

        # 更新刷新时间
        from datetime import datetime
        account.refresh_time = credentials.get("refresh_time") or datetime.now().isoformat()

        # 标记为可用
        account.available = True

        # 保存配置
        config_manager.save()

        # 同步更新 AccountManager 中的账号信息
        try:
            from app.services.account_manager import account_manager
            account_manager.reload_account(account_index)
        except Exception as e:
            logger.warning(f"同步更新 AccountManager 失败: {e}")

    async def check_and_refresh(self, account_index: int) -> Tuple[bool, str]:
        """
        检查凭证，如果无效则尝试刷新

        Args:
            account_index: 账号索引

        Returns:
            (is_valid, error_message)
        """
        # 首先检查凭证
        is_valid, error = await self.check_credential(account_index)

        if is_valid:
            return True, ""

        # 凭证无效，尝试刷新
        logger.warning(f"账号 {account_index} 凭证无效 ({error})，尝试自动刷新")

        if not self._auto_login_service:
            return False, f"凭证无效且自动刷新未启用: {error}"

        refresh_success, refresh_error = await self.refresh_credential(account_index)

        if refresh_success:
            return True, ""
        else:
            return False, f"凭证刷新失败: {refresh_error}"

    def is_refreshing(self, account_index: int) -> bool:
        """检查账号是否正在刷新"""
        return account_index in self._refreshing

    def is_known_invalid(self, account_index: int) -> bool:
        """
        快速检查账号是否已知无效（不阻塞）

        用于账号选择时快速跳过已知无效的账号
        """
        return account_index in self._invalid_accounts

    def mark_invalid(self, account_index: int):
        """标记账号凭证无效"""
        self._invalid_accounts.add(account_index)
        account = config_manager.get_account(account_index)
        account_note = account.note if account else f"Account{account_index}"
        logger.info(f"Account {account_index} ({account_note}) marked as invalid")

    async def queue_refresh(self, account_index: int):
        """
        将账号加入后台刷新队列（非阻塞）

        Args:
            account_index: 账号索引
        """
        account = config_manager.get_account(account_index)
        account_note = account.note if account else f"Account{account_index}"

        if not self._initialized or not self._refresh_queue:
            logger.warning(f"Service not initialized, Account {account_index} cannot be queued")
            return

        if not self._auto_login_service:
            logger.debug(f"auto_login not enabled, Account {account_index} skip")
            return

        if account_index in self._refreshing:
            logger.debug(f"Account {account_index} already refreshing")
            return

        if account_index in self._queued_accounts:
            logger.debug(f"Account {account_index} already in queue")
            return

        now = time.time()
        last_refresh = self._last_refresh_time.get(account_index, 0)
        if now - last_refresh < self._refresh_interval:
            logger.debug(f"Account {account_index} in cooldown")
            return

        self._invalid_accounts.add(account_index)

        try:
            self._queued_accounts.add(account_index)
            self._refresh_queue.put_nowait(account_index)
            logger.info(f"Account {account_index} ({account_note}) queued for refresh")
        except asyncio.QueueFull:
            self._queued_accounts.discard(account_index)
            logger.warning(f"Refresh queue full, Account {account_index} cannot be added")

    async def quick_check_and_queue(self, account_index: int) -> bool:
        """
        快速检查凭证，如果无效则加入刷新队列（非阻塞）

        Args:
            account_index: 账号索引

        Returns:
            True 如果凭证有效，False 如果无效（已加入刷新队列）
        """
        # 检查是否最近检查过
        now = time.time()
        last_check = self._last_check_time.get(account_index, 0)
        if now - last_check < self._check_interval:
            # 最近检查过，假设仍然有效（除非已知无效）
            return account_index not in self._invalid_accounts

        # 执行检查
        is_valid, error = await self.check_credential(account_index)
        self._last_check_time[account_index] = now

        if is_valid:
            # 从无效列表移除
            self._invalid_accounts.discard(account_index)
            return True
        else:
            # 加入刷新队列
            logger.warning(f"账号 {account_index} 凭证无效: {error}，加入刷新队列")
            await self.queue_refresh(account_index)
            return False

    def get_status(self) -> dict:
        """获取服务状态"""
        status = {
            "initialized": self._initialized,
            "auto_login_enabled": self._config_dict is not None,
            "concurrent_mode": True,
            "max_concurrent": self._max_concurrent,
            "active_tasks": len(self._active_tasks),
            "active_task_ids": list(self._active_tasks.keys()),
            "background_task_running": self._background_task is not None and not self._background_task.done(),
            "shared_resources_initialized": self._browser is not None,
            "code_hub_running": self._code_hub is not None,
            # 刷新相关
            "refreshing_accounts": list(self._refreshing),
            "queued_refresh": list(self._queued_accounts),
            "refresh_queue_size": self._refresh_queue.qsize() if self._refresh_queue else 0,
            "invalid_accounts": list(self._invalid_accounts),
            # 注册相关
            "registering_emails": list(self._registering),
            "queued_register": list(self._queued_emails),
            "register_queue_size": self._register_queue.qsize() if self._register_queue else 0,
            "refresh_interval": self._refresh_interval,
            "last_refresh_times": {
                k: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v))
                for k, v in self._last_refresh_time.items()
            }
        }

        # 添加验证码中心状态
        if self._code_hub:
            status["code_hub_status"] = self._code_hub.get_status()

        return status

    def _load_emails_from_file(self, file_path: str = None) -> list:
        """
        从凭证文件加载邮箱列表

        Args:
            file_path: 文件路径，默认使用配置中的 credentials_file

        Returns:
            邮箱列表
        """
        if file_path is None:
            # 使用配置中的文件路径
            config_path = config_manager.config.credentials_file
            if config_path:
                # 如果是相对路径，相对于项目根目录
                if not Path(config_path).is_absolute():
                    file_path = Path(__file__).parent.parent.parent / config_path
                else:
                    file_path = Path(config_path)
            else:
                file_path = Path(__file__).parent.parent.parent / "credient.txt"
        else:
            file_path = Path(file_path)

        if not file_path.exists():
            logger.warning(f"凭证文件不存在: {file_path}")
            return []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            emails = []
            for line in lines:
                line = line.strip()
                # 跳过空行和注释
                if line and not line.startswith("#"):
                    # 验证邮箱格式
                    if "@" in line:
                        emails.append(line)

            return emails
        except Exception as e:
            logger.error(f"读取凭证文件失败: {e}")
            return []

    def _get_configured_emails(self) -> set:
        """获取已配置的账号邮箱集合（通过 note 字段匹配）"""
        configured = set()
        for acc in config_manager.config.accounts:
            # note 字段通常包含邮箱前缀或完整邮箱
            if acc.note:
                configured.add(acc.note.lower())
        return configured

    def _find_account_by_email(self, email: str):
        """
        通过邮箱查找已配置的账号

        使用精确匹配（完整邮箱或邮箱前缀），避免模糊匹配导致误判
        """
        email_lower = email.lower()
        email_prefix = email.split("@")[0].lower()

        for i, acc in enumerate(config_manager.config.accounts):
            note_lower = acc.note.lower() if acc.note else ""
            # 完整邮箱匹配
            if email_lower == note_lower:
                return i, acc
            # 邮箱前缀精确匹配
            if email_prefix == note_lower:
                return i, acc

        return None, None

    async def sync_accounts_from_file(
        self,
        file_path: str = None,
        refresh_invalid: bool = True,
        register_new: bool = True,
        max_concurrent: int = 5
    ) -> dict:
        """
        从凭证文件并发同步账号

        读取 credient.txt 中的所有邮箱，对于：
        - 未配置的账号：执行注册/登录并添加到配置
        - 已配置但凭证无效的账号：刷新凭证

        Args:
            file_path: 凭证文件路径，默认 credient.txt
            refresh_invalid: 是否刷新已失效的账号
            register_new: 是否注册新账号
            max_concurrent: 最大并发数（默认5）

        Returns:
            同步结果统计
        """
        if not self._config_dict:
            return {
                "success": False,
                "error": "auto_login not configured",
                "new_accounts": 0,
                "refreshed_accounts": 0,
                "failed_accounts": 0
            }

        # 加载邮箱列表
        emails = self._load_emails_from_file(file_path)
        if not emails:
            return {
                "success": False,
                "error": "Credentials file empty or not found",
                "new_accounts": 0,
                "refreshed_accounts": 0,
                "failed_accounts": 0
            }

        logger.info(f"Loaded {len(emails)} emails from credentials file")

        # 分类邮箱：新账号 vs 已有账号
        new_emails = []
        existing_accounts = []  # (account_index, email)

        for email in emails:
            account_index, account_config = self._find_account_by_email(email)
            if account_config:
                existing_accounts.append((account_index, email))
            else:
                new_emails.append(email)

        print(f"\n[Sync] Total: {len(emails)} | New: {len(new_emails)} | Existing: {len(existing_accounts)}")

        results = {
            "success": True,
            "total_emails": len(emails),
            "new_accounts": 0,
            "refreshed_accounts": 0,
            "failed_accounts": 0,
            "skipped_accounts": 0,
            "details": []
        }

        # 清除之前的注册结果
        self._register_results.clear()

        # 处理新账号注册
        if register_new and new_emails:
            print(f"[Sync] Queuing {len(new_emails)} new accounts for registration...")

            # 等待注册完成（wait_for_registrations 会自动将邮箱加入队列）
            register_results = await self.wait_for_registrations(
                new_emails,
                timeout=len(new_emails) * 180 + 120  # 每个账号最多3分钟 + 初始化时间
            )

            for email, result in register_results.items():
                if result["success"]:
                    results["new_accounts"] += 1
                    results["details"].append({
                        "email": email,
                        "action": "registered",
                        "success": True,
                        "message": "Registration successful"
                    })
                else:
                    results["failed_accounts"] += 1
                    results["details"].append({
                        "email": email,
                        "action": "failed",
                        "success": False,
                        "message": result.get("error", "Unknown error")
                    })

        # 处理已有账号刷新
        if refresh_invalid and existing_accounts:
            print(f"[Sync] Checking {len(existing_accounts)} existing accounts...")

            for account_index, email in existing_accounts:
                # 检查凭证是否有效
                is_valid, error = await self.check_credential(account_index)
                if is_valid:
                    results["skipped_accounts"] += 1
                    results["details"].append({
                        "email": email,
                        "action": "skipped",
                        "success": True,
                        "message": "Credentials valid"
                    })
                else:
                    # 加入刷新队列
                    await self.queue_refresh(account_index)

            # 等待刷新完成（简单等待一段时间，刷新队列由worker处理）
            await asyncio.sleep(5)

            # 检查结果（刷新是异步的，这里只记录已触发）
            for account_index, email in existing_accounts:
                if account_index in self._refreshing or account_index in self._queued_accounts:
                    # 还在刷新中
                    pass
                elif account_index not in self._invalid_accounts:
                    # 已完成且成功
                    results["refreshed_accounts"] += 1
                    results["details"].append({
                        "email": email,
                        "action": "refreshed",
                        "success": True,
                        "message": "Refresh triggered"
                    })

        print(f"[Sync] Complete: {results['new_accounts']} registered, {results['refreshed_accounts']} refreshed, {results['failed_accounts']} failed")

        return results

    async def _process_single_email(
        self,
        email: str,
        refresh_invalid: bool,
        register_new: bool
    ) -> dict:
        """
        处理单个邮箱

        Args:
            email: 邮箱地址
            refresh_invalid: 是否刷新无效账号
            register_new: 是否注册新账号

        Returns:
            处理结果
        """
        result = {
            "email": email,
            "action": "skipped",
            "success": False,
            "message": ""
        }

        # 查找是否已有此账号
        account_index, account_config = self._find_account_by_email(email)

        if account_config:
            # 账号已存在
            if refresh_invalid:
                # 检查凭证是否有效
                print(f"\n[同步] 检查账号 {email} 凭证...")
                is_valid, error = await self.check_credential(account_index)
                if is_valid:
                    result["action"] = "skipped"
                    result["success"] = True
                    result["message"] = "凭证有效，无需刷新"
                    print(f"[同步] [{email}] 凭证有效，跳过")
                else:
                    # 需要刷新
                    print(f"[同步] [{email}] 凭证无效，开始刷新...")
                    logger.info(f"[{email}] 凭证无效，开始刷新...")
                    success, error = await self.refresh_credential(account_index)
                    if success:
                        result["action"] = "refreshed"
                        result["success"] = True
                        result["message"] = "凭证刷新成功"
                        print(f"[同步] [{email}] 刷新成功!")
                    else:
                        result["action"] = "failed"
                        result["success"] = False
                        result["message"] = f"刷新失败: {error}"
                        print(f"[同步] [{email}] 刷新失败: {error}")
            else:
                result["action"] = "skipped"
                result["success"] = True
                result["message"] = "账号已存在"
                print(f"[同步] [{email}] 账号已存在，跳过")
        else:
            # 新账号
            if register_new:
                print(f"\n[同步] [{email}] 新账号，开始注册...")
                logger.info(f"[{email}] 新账号，开始注册...")
                credentials = await self._auto_login_service.register_new_account(
                    google_email=email,
                    note=email.split("@")[0]
                )

                if credentials:
                    # 添加到配置
                    self._add_account_to_config(credentials)
                    result["action"] = "registered"
                    result["success"] = True
                    result["message"] = "注册成功"
                    print(f"[同步] [{email}] 注册成功!")
                else:
                    result["action"] = "failed"
                    result["success"] = False
                    result["message"] = "注册失败"
                    print(f"[同步] [{email}] 注册失败")
            else:
                result["action"] = "skipped"
                result["success"] = True
                result["message"] = "跳过新账号注册"

        return result

    def _add_account_to_config(self, credentials: dict):
        """
        将新账号添加到配置

        Args:
            credentials: 凭证字典
        """
        from app.config import AccountConfig

        note = credentials.get("note", credentials.get("google_email", "").split("@")[0])

        new_account = AccountConfig(
            team_id=credentials.get("team_id", ""),
            csesidx=credentials.get("csesidx", ""),
            secure_c_ses=credentials.get("secure_c_ses", ""),
            host_c_oses=credentials.get("host_c_oses", ""),
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            note=note,
            available=True
        )

        config_manager.config.accounts.append(new_account)
        config_manager.save()

        # 重新加载账号管理器
        try:
            from app.services.account_manager import account_manager
            account_manager.load_accounts()
        except:
            pass

        print(f"[同步] 新账号已添加到配置: {note} (team_id: {credentials.get('team_id', '')[:20]}...)")
        logger.info(f"新账号已添加到配置: {note}")

    async def sync_single_account(self, email: str) -> dict:
        """
        同步单个账号

        如果账号已存在则刷新凭证，不存在则注册

        Args:
            email: 邮箱地址

        Returns:
            同步结果
        """
        if not self._auto_login_service:
            return {
                "success": False,
                "error": "自动登录服务未启用"
            }

        return await self._process_single_email(
            email,
            refresh_invalid=True,
            register_new=True
        )

    async def sync_accounts_concurrent(
        self,
        file_path: str = None,
        max_concurrent: int = 5,
        refresh_only: bool = False
    ) -> dict:
        """
        并发同步账号（高速模式）

        使用并发服务同时刷新多个账号，大幅提升效率。
        验证码通过中心化轮询分发，避免冲突。

        Args:
            file_path: 凭证文件路径，默认 credient.txt
            max_concurrent: 最大并发数（默认 5）
            refresh_only: 仅刷新现有账号（不注册新账号）

        Returns:
            同步结果统计
        """
        global _concurrent_service

        # 加载邮箱列表
        emails = self._load_emails_from_file(file_path)
        if not emails:
            return {
                "success": False,
                "error": "凭证文件为空或不存在",
                "total": 0,
                "refreshed": 0,
                "failed": 0
            }

        # 筛选需要刷新的账号
        accounts_to_refresh = []

        for email in emails:
            account_index, account_config = self._find_account_by_email(email)

            if account_config:
                # 已有账号，检查是否需要刷新
                google_email = email
                accounts_to_refresh.append((account_config, google_email))
            elif not refresh_only:
                # 新账号，需要注册（但并发模式目前只支持刷新）
                logger.warning(f"[并发刷新] 跳过新账号 {email}（并发模式暂不支持注册）")

        if not accounts_to_refresh:
            return {
                "success": True,
                "message": "没有需要刷新的账号",
                "total": len(emails),
                "refreshed": 0,
                "failed": 0
            }

        # 初始化并发服务
        try:
            from app.services.auto_login.concurrent_service import ConcurrentAutoLoginService

            config = config_manager.config
            auto_login_config = config.auto_login

            if not auto_login_config or not auto_login_config.enabled:
                return {
                    "success": False,
                    "error": "自动登录服务未启用",
                    "total": len(accounts_to_refresh),
                    "refreshed": 0,
                    "failed": 0
                }

            config_dict = {
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
            }

            # 创建并发服务
            if _concurrent_service is None:
                _concurrent_service = ConcurrentAutoLoginService(config_dict, max_concurrent)
                await _concurrent_service.initialize()

            print(f"\n[并发刷新] 开始刷新 {len(accounts_to_refresh)} 个账号 (并发数={max_concurrent})")
            logger.info(f"开始并发刷新 {len(accounts_to_refresh)} 个账号")

            # 执行并发刷新
            results = await _concurrent_service.refresh_accounts(accounts_to_refresh)

            # 更新成功的账号凭证
            for item in results.get("success", []):
                email = item.get("email")
                credentials = item.get("credentials")
                if credentials:
                    account_index, _ = self._find_account_by_email(email)
                    if account_index is not None:
                        self._update_account_credentials(account_index, credentials)
                        # 从无效列表移除
                        self._invalid_accounts.discard(account_index)

            return {
                "success": True,
                "total": results.get("total", len(accounts_to_refresh)),
                "refreshed": len(results.get("success", [])),
                "failed": len(results.get("failed", [])),
                "details": {
                    "success": [item.get("email") for item in results.get("success", [])],
                    "failed": [
                        {"email": item.get("email"), "error": item.get("error")}
                        for item in results.get("failed", [])
                    ]
                }
            }

        except ImportError as e:
            return {
                "success": False,
                "error": f"并发服务模块未安装: {e}",
                "total": len(accounts_to_refresh),
                "refreshed": 0,
                "failed": 0
            }
        except Exception as e:
            logger.error(f"并发刷新出错: {e}")
            return {
                "success": False,
                "error": str(e),
                "total": len(accounts_to_refresh),
                "refreshed": 0,
                "failed": 0
            }

    async def shutdown_concurrent_service(self):
        """关闭并发刷新服务（包括共享资源）"""
        global _concurrent_service

        # 关闭独立的并发服务（如果有）
        if _concurrent_service:
            await _concurrent_service.close()
            _concurrent_service = None
            logger.info("独立并发刷新服务已关闭")

        # 关闭内置的共享资源
        await self._close_shared_resources()
        logger.info("共享刷新资源已关闭")


# 全局凭证服务实例
credential_service = CredentialRefreshService()
