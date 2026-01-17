"""
YesCaptcha 打码服务

用于解决 Google reCAPTCHA v3 验证，提高验证码发送成功率
"""
import asyncio
import logging
import re
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# YesCaptcha API 配置
YESCAPTCHA_CREATE_TASK_URL = "https://api.yescaptcha.com/createTask"
YESCAPTCHA_GET_RESULT_URL = "https://api.yescaptcha.com/getTaskResult"

# Gemini Business 验证页面的 reCAPTCHA 配置
RECAPTCHA_WEBSITE_KEY = "6Ld8dCcrAAAAAFVbDMVZy8aNRwCjakBVaDEdRUH8"
RECAPTCHA_WEBSITE_URL = "https://accountverification.business.gemini.google"
RECAPTCHA_PAGE_ACTION = "verify_oob_code"

# Token 正则匹配（用于替换请求中的 captcha token）
CAPTCHA_TOKEN_PATTERN = re.compile(r'0[3c]AFc[a-zA-Z0-9_\-]{50,}')


class YesCaptchaService:
    """
    YesCaptcha 打码服务

    当 Google 检测到自动化操作拦截验证码发送时，
    使用 YesCaptcha 获取有效的 reCAPTCHA v3 token 来绕过检测
    """

    def __init__(self, api_key: str):
        """
        初始化服务

        Args:
            api_key: YesCaptcha API 密钥
        """
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_captcha_token(
        self,
        website_url: str = RECAPTCHA_WEBSITE_URL,
        website_key: str = RECAPTCHA_WEBSITE_KEY,
        page_action: str = RECAPTCHA_PAGE_ACTION,
        max_wait: int = 60
    ) -> Optional[str]:
        """
        获取 reCAPTCHA v3 token

        Args:
            website_url: 目标网站 URL
            website_key: reCAPTCHA 网站密钥
            page_action: 页面动作
            max_wait: 最大等待时间（秒）

        Returns:
            reCAPTCHA token，失败返回 None
        """
        if not self.api_key:
            logger.warning("YesCaptcha API key 未配置")
            return None

        client = await self._get_client()

        try:
            print(f"  [YesCaptcha] 正在请求 reCAPTCHA v3 token...")
            logger.info(f"请求 YesCaptcha: website={website_url}, action={page_action}")

            # 创建任务
            create_response = await client.post(
                YESCAPTCHA_CREATE_TASK_URL,
                json={
                    "clientKey": self.api_key,
                    "task": {
                        "websiteURL": website_url,
                        "websiteKey": website_key,
                        "pageAction": page_action,
                        "type": "RecaptchaV3TaskProxylessM1"
                    }
                }
            )

            create_data = create_response.json()

            if create_data.get("errorId"):
                error_msg = create_data.get("errorDescription", "未知错误")
                print(f"  [YesCaptcha] 创建任务失败: {error_msg}")
                logger.error(f"YesCaptcha 创建任务失败: {error_msg}")
                return None

            task_id = create_data.get("taskId")
            if not task_id:
                print(f"  [YesCaptcha] 未获取到任务 ID")
                logger.error("YesCaptcha 未返回 taskId")
                return None

            print(f"  [YesCaptcha] 任务已创建: {task_id}")

            # 轮询获取结果
            poll_interval = 3  # 每 3 秒查询一次
            max_polls = max_wait // poll_interval

            for i in range(max_polls):
                await asyncio.sleep(poll_interval)

                result_response = await client.post(
                    YESCAPTCHA_GET_RESULT_URL,
                    json={
                        "clientKey": self.api_key,
                        "taskId": task_id
                    }
                )

                result_data = result_response.json()

                if result_data.get("errorId"):
                    error_msg = result_data.get("errorDescription", "未知错误")
                    print(f"  [YesCaptcha] 获取结果失败: {error_msg}")
                    logger.error(f"YesCaptcha 获取结果失败: {error_msg}")
                    return None

                status = result_data.get("status")

                if status == "ready":
                    token = result_data.get("solution", {}).get("gRecaptchaResponse")
                    if token:
                        print(f"  [YesCaptcha] Token 获取成功!")
                        logger.info(f"YesCaptcha token 获取成功，长度: {len(token)}")
                        return token
                    else:
                        print(f"  [YesCaptcha] 结果中无 token")
                        return None

                elif status == "processing":
                    if (i + 1) % 5 == 0:
                        print(f"  [YesCaptcha] 处理中... [{(i+1) * poll_interval}/{max_wait}秒]")
                    continue

                else:
                    print(f"  [YesCaptcha] 未知状态: {status}")

            print(f"  [YesCaptcha] 获取 token 超时 ({max_wait}秒)")
            logger.warning(f"YesCaptcha 获取 token 超时")
            return None

        except Exception as e:
            print(f"  [YesCaptcha] 请求出错: {e}")
            logger.error(f"YesCaptcha 请求出错: {e}")
            return None

    def patch_payload(self, raw_body: str, new_token: str) -> str:
        """
        替换请求体中的 captcha token

        Args:
            raw_body: 原始请求体（URL 编码格式）
            new_token: 新的 captcha token

        Returns:
            替换后的请求体
        """
        if not raw_body or not new_token:
            return raw_body

        try:
            from urllib.parse import parse_qs, urlencode

            # 解析 URL 编码的请求体
            params = parse_qs(raw_body, keep_blank_values=True)

            f_req = params.get("f.req", [""])[0]
            if not f_req:
                return raw_body

            # 检查是否包含 captcha token
            if not CAPTCHA_TOKEN_PATTERN.search(f_req):
                logger.warning("请求体中未找到 captcha token")
                return raw_body

            # 替换 token
            patched_f_req = CAPTCHA_TOKEN_PATTERN.sub(new_token, f_req)

            # 重新编码
            params["f.req"] = [patched_f_req]

            # 构建新的请求体
            result_parts = []
            for key, values in params.items():
                for value in values:
                    result_parts.append(f"{key}={value}")

            return "&".join(result_parts)

        except Exception as e:
            logger.error(f"替换 token 失败: {e}")
            return raw_body


class CaptchaInterceptor:
    """
    Captcha 拦截器

    监控网络请求，检测 CAPTCHA_CHECK_FAILED 错误并自动打码重试
    """

    def __init__(self, page, captcha_service: YesCaptchaService):
        """
        初始化拦截器

        Args:
            page: Playwright 页面对象
            captcha_service: YesCaptcha 服务实例
        """
        self.page = page
        self.captcha_service = captcha_service
        self._code_sent = asyncio.Event()
        self._captcha_handled = asyncio.Event()
        self._last_batch_request = None  # 保存最后一次 batchexecute 请求

    async def start_monitoring(self):
        """开始监控网络响应"""
        self.page.on("response", self._on_response)
        self.page.on("request", self._on_request)
        print(f"  [拦截器] 开始监控网络响应...")

    def stop_monitoring(self):
        """停止监控"""
        try:
            self.page.remove_listener("response", self._on_response)
            self.page.remove_listener("request", self._on_request)
        except:
            pass

    async def _on_request(self, request):
        """记录 batchexecute 请求"""
        url = request.url
        if "batchexecute" in url:
            self._last_batch_request = {
                "url": url,
                "headers": request.headers,
                "post_data": request.post_data
            }

    async def _on_response(self, response):
        """处理网络响应"""
        url = response.url

        if "batchexecute" not in url:
            return

        try:
            text = await response.text()

            # 检测验证码发送成功
            if "LookupVerifiedEmail" in text or "SendVerificationCode" in text:
                print(f"  [拦截器] 检测到验证码发送成功!")
                self._code_sent.set()
                return

            # 检测 CAPTCHA 拦截
            if "CAPTCHA_CHECK_FAILED" in text:
                print(f"  [拦截器] 检测到 CAPTCHA 拦截，开始打码...")
                await self._handle_captcha_failure()
                return

            # 其他成功响应
            if "inner_api_status" in text:
                self._captcha_handled.set()

        except Exception as e:
            logger.debug(f"处理响应时出错: {e}")

    async def _handle_captcha_failure(self):
        """处理 CAPTCHA 拦截"""
        if not self._last_batch_request:
            print(f"  [拦截器] 未找到原始请求，无法重试")
            return

        # 获取新的 captcha token
        new_token = await self.captcha_service.get_captcha_token()
        if not new_token:
            print(f"  [拦截器] 获取 token 失败")
            return

        # 替换请求中的 token
        original_post_data = self._last_batch_request.get("post_data", "")
        patched_post_data = self.captcha_service.patch_payload(original_post_data, new_token)

        if patched_post_data == original_post_data:
            print(f"  [拦截器] Token 替换失败")
            return

        # 重发请求
        url = self._last_batch_request["url"]
        headers = self._last_batch_request["headers"]

        print(f"  [拦截器] 正在重发带 token 的请求...")

        try:
            # 在页面中执行 fetch 重发请求
            await self.page.evaluate(
                """
                async ([url, headers, body]) => {
                    await fetch(url, {
                        method: 'POST',
                        headers: headers,
                        body: body,
                        credentials: 'include'
                    });
                }
                """,
                [url, headers, patched_post_data]
            )
            print(f"  [拦截器] 请求已重发!")
            self._captcha_handled.set()

        except Exception as e:
            print(f"  [拦截器] 重发请求失败: {e}")

    async def wait_for_code_sent(self, timeout: float = 30) -> bool:
        """
        等待验证码发送成功

        Args:
            timeout: 超时时间（秒）

        Returns:
            是否成功
        """
        try:
            await asyncio.wait_for(self._code_sent.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_for_captcha_handled(self, timeout: float = 60) -> bool:
        """
        等待 CAPTCHA 处理完成

        Args:
            timeout: 超时时间（秒）

        Returns:
            是否成功
        """
        try:
            await asyncio.wait_for(self._captcha_handled.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def is_code_sent(self) -> bool:
        """验证码是否已发送"""
        return self._code_sent.is_set()

    def reset(self):
        """重置状态"""
        self._code_sent.clear()
        self._captcha_handled.clear()
        self._last_batch_request = None
