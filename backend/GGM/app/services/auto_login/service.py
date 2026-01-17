"""
自动登录服务

使用 Playwright 和邮箱验证码自动完成 Google 登录

完全参考 extract_credentials.py 实现
"""
import asyncio
import re
import time
import random
from datetime import datetime
from typing import Optional, Dict, Any
from urllib.parse import urlparse, parse_qs

from app.config import AccountConfig
from .human_behavior import HumanBehavior, setup_stealth_browser, inject_stealth_scripts
from .captcha_service import YesCaptchaService, CaptchaInterceptor


class GoogleAutoLogin:
    """
    Google 自动登录服务

    使用 Playwright 自动化 Google 登录流程（仅邮箱+验证码，无密码）
    """

    # Google 登录页面元素选择器
    SELECTORS = {
        # Gemini Business 登录页面（auth.business.gemini.google）
        "gemini_email_input": '#email-input',
        "gemini_login_button": '#log-in-button',

        # Google 标准登录页面（accounts.google.com）
        "google_email_input": 'input[type="email"]',
        "google_email_next_button": '#identifierNext',

        # Gemini Business 验证码页面（accountverification.business.gemini.google）
        # 6个独立的输入框
        "gemini_verification_inputs": 'input[type="text"]',
        "gemini_verify_button": 'button:has-text("验证")',

        # Google 标准验证码输入页面
        "verification_inputs": [
            'input[name="code"]',
            'input[type="tel"]',
            'input[autocomplete="one-time-code"]',
            'input[name="totpPin"]',
            'input[name="Pin"]',
            'input#code',
        ],
        "verification_next_buttons": [
            '#idvPreregisteredPhoneNext',
            'button[type="submit"]',
            'button:has-text("Next")',
            'button:has-text("下一步")',
            'div[role="button"]:has-text("Next")',
            'div[role="button"]:has-text("下一步")',
        ],
    }

    # 登录成功判断
    LOGIN_SUCCESS_INDICATORS = [
        "business.gemini.google/home",
        "business.gemini.google/cid",
    ]

    # 需要登录的页面
    LOGIN_PAGE_INDICATORS = [
        "auth.business.gemini.google",
        "accounts.google.com",
    ]

    # 验证码页面
    VERIFICATION_PAGE_INDICATORS = [
        "accountverification.business.gemini.google",
        "accounts.google.com/v2/challenge",
    ]

    # 首次使用注册页面
    TRIAL_SIGNUP_INDICATORS = [
        "business.gemini.google/admin/create",
    ]

    # 错误页面（需要重试）
    ERROR_PAGE_INDICATORS = [
        "signin-error",
        "请试试其他方法",
        "Try another way",
        "Something went wrong",
    ]

    def __init__(self, page, email_service, timeout: int = 30000, captcha_service: YesCaptchaService = None):
        """
        初始化登录服务

        Args:
            page: Playwright 页面对象
            email_service: 邮件验证码服务
            timeout: 默认超时时间（毫秒）
            captcha_service: YesCaptcha 打码服务（可选，用于绕过 reCAPTCHA）
        """
        self.page = page
        self.email_service = email_service
        self.timeout = timeout
        self._current_email = None  # 当前登录的邮箱
        self.human = HumanBehavior(page)  # 人类行为模拟器
        self.captcha_service = captcha_service  # 打码服务
        self._captcha_interceptor = None  # 验证码拦截器

    async def login(self, google_email: str, verification_timeout: int = 120, max_retries: int = 3) -> bool:
        """
        执行完整的 Google 登录流程（无密码）

        支持错误检测和自动重试

        Args:
            google_email: Google 邮箱
            verification_timeout: 验证码等待超时（秒）
            max_retries: 最大重试次数

        Returns:
            登录是否成功
        """
        print(f"  [登录] 开始自动登录: {google_email}")
        self._current_email = google_email  # 保存当前邮箱用于注册页面

        for retry in range(max_retries):
            if retry > 0:
                # 重试时增加等待时间，避免频繁操作
                wait_time = (retry + 1) * 5
                print(f"  [登录] 第 {retry + 1} 次重试，等待 {wait_time} 秒...")
                await asyncio.sleep(wait_time)

            try:
                result = await self._do_login(google_email, verification_timeout)
                if result:
                    return True

                # 检查是否因为错误页面失败
                if await self._is_error_page():
                    print(f"  [登录] 检测到错误页面，准备重试...")
                    await self._handle_error_page()
                    continue
                else:
                    # 其他原因失败，也尝试重试
                    print(f"  [登录] 登录失败，准备重试...")
                    continue

            except Exception as e:
                print(f"  [登录] [!] 登录过程出错: {e}")
                import traceback
                traceback.print_exc()
                if retry < max_retries - 1:
                    continue
                return False

        print(f"  [登录] [!] 达到最大重试次数 ({max_retries})，登录失败")
        return False

    async def _do_login(self, google_email: str, verification_timeout: int) -> bool:
        """
        执行实际的登录流程（内部方法）

        Args:
            google_email: Google 邮箱
            verification_timeout: 验证码等待超时

        Returns:
            登录是否成功
        """
        current_url = self.page.url
        print(f"  [登录] 当前页面: {current_url}")

        # 预热浏览器会话，提高 reCAPTCHA 评分
        print(f"  [登录] 预热浏览器会话...")
        await self.human.warm_up_session(duration=5)

        # 首先检查是否在错误页面
        if await self._is_error_page():
            print("  [登录] 检测到错误页面...")
            await self._handle_error_page()
            current_url = self.page.url

        # 首先检查是否在首次注册页面
        if await self._is_trial_signup_page():
            print("  [登录] 检测到首次使用注册页面...")
            display_name = google_email.split("@")[0] if google_email else "Gemini User"
            if not await self._handle_trial_signup(display_name):
                return False
            # 注册后等待跳转
            return await self._wait_for_login_success()

        # 判断当前在哪个登录页面
        if "auth.business.gemini.google" in current_url:
            # Gemini Business 自己的登录页面
            print(f"  [登录] 检测到 Gemini Business 登录页面")
            if not await self._enter_email_gemini(google_email):
                return False
        elif "accounts.google.com" in current_url:
            # Google 标准登录页面
            print(f"  [登录] 检测到 Google 标准登录页面")
            if not await self._enter_email_google(google_email):
                return False
        else:
            print(f"  [登录] [!] 未知的登录页面: {current_url}")
            return False

        # 等待页面跳转（使用随机延迟）
        print(f"  [登录] 等待页面跳转...")
        await self.human.wait_random(3000, 5000)
        current_url = self.page.url
        print(f"  [登录] 跳转后页面: {current_url}")

        # 检查是否出现错误页面
        if await self._is_error_page():
            print("  [登录] 输入邮箱后出现错误页面...")
            return False

        # 检查是否跳转到注册页面
        if await self._is_trial_signup_page():
            print("  [登录] 检测到首次使用注册页面...")
            display_name = google_email.split("@")[0] if google_email else "Gemini User"
            if not await self._handle_trial_signup(display_name):
                return False
            # 注册后等待跳转
            return await self._wait_for_login_success()

        # 记录请求验证码的时间
        request_time = datetime.now()
        print(f"  [登录] 开始检测验证码页面 (最多等待15秒)...")

        # 检查是否需要验证码（可能跳转到 Google 验证码页面）
        for i in range(15):
            await asyncio.sleep(1)
            current_url = self.page.url

            # 检查是否出现错误页面
            if await self._is_error_page():
                print("  [登录] 等待过程中检测到错误页面...")
                return False

            # 检查是否已经登录成功
            if any(indicator in current_url for indicator in self.LOGIN_SUCCESS_INDICATORS):
                print("  [登录] 会话有效，无需验证码，登录成功!")
                return True

            # 检查是否在注册页面
            if await self._is_trial_signup_page():
                print("  [登录] 检测到首次使用注册页面...")
                display_name = google_email.split("@")[0] if google_email else "Gemini User"
                if not await self._handle_trial_signup(display_name):
                    return False
                break

            # 检查是否在验证码页面
            is_verification = await self._is_verification_page()
            if i % 3 == 0:  # 每3秒输出一次状态
                print(f"  [登录] 检测验证码页面... [{i+1}/15] 是验证码页面={is_verification}")

            if is_verification:
                print("  [登录] 确认进入验证码页面，开始处理验证码...")
                if not await self._handle_verification(request_time, verification_timeout, google_email):
                    return False
                break

        # 等待登录完成
        print(f"  [登录] 等待登录完成...")
        return await self._wait_for_login_success()

    async def _enter_email_gemini(self, google_email: str) -> bool:
        """
        在 Gemini Business 登录页面输入邮箱（使用人类行为模拟）

        Args:
            google_email: 邮箱地址

        Returns:
            是否成功
        """
        try:
            print("  在 Gemini Business 登录页面输入邮箱...")

            # 随机等待，模拟人类阅读页面
            await self.human.wait_random(1000, 2500)

            # 等待邮箱输入框
            await self.page.wait_for_selector(
                self.SELECTORS["gemini_email_input"],
                timeout=self.timeout
            )

            # 获取输入框元素
            email_input = await self.page.query_selector(self.SELECTORS["gemini_email_input"])

            # 随机鼠标移动（模拟人类浏览页面）
            await self.human.random_mouse_movement(random.randint(1, 3))

            # 使用人类打字模拟输入邮箱
            await self.human.type_like_human(email_input, google_email, speed="human")

            print(f"  已输入邮箱: {google_email}")

            # 短暂停顿后点击按钮
            await self.human.wait_random(300, 800)

            # 点击登录按钮（使用人类点击）
            login_button = await self.page.query_selector(self.SELECTORS["gemini_login_button"])
            if login_button:
                await self.human.human_click(login_button)
                print("  已点击登录按钮")
            else:
                # 尝试按回车
                await self.page.keyboard.press("Enter")
                print("  已按回车")

            return True

        except Exception as e:
            print(f"  [!] 输入邮箱时出错: {e}")
            return False

    async def _enter_email_google(self, google_email: str) -> bool:
        """
        在 Google 标准登录页面输入邮箱（使用人类行为模拟）

        Args:
            google_email: 邮箱地址

        Returns:
            是否成功
        """
        try:
            print("  在 Google 登录页面输入邮箱...")

            # 随机等待，模拟人类阅读页面
            await self.human.wait_random(1000, 2500)

            # 等待邮箱输入框
            await self.page.wait_for_selector(
                self.SELECTORS["google_email_input"],
                timeout=self.timeout
            )

            # 获取输入框元素
            email_input = await self.page.query_selector(self.SELECTORS["google_email_input"])

            # 随机鼠标移动
            await self.human.random_mouse_movement(random.randint(1, 3))

            # 使用人类打字模拟输入邮箱
            await self.human.type_like_human(email_input, google_email, speed="human")

            print(f"  已输入邮箱: {google_email}")

            # 短暂停顿后点击按钮
            await self.human.wait_random(300, 800)

            # 点击下一步（使用人类点击）
            next_button = await self.page.query_selector(self.SELECTORS["google_email_next_button"])
            if next_button:
                await self.human.human_click(next_button)
                print("  已点击下一步")
            else:
                await self.page.keyboard.press("Enter")
                print("  已按回车")

            return True

        except Exception as e:
            print(f"  [!] 输入邮箱时出错: {e}")
            return False

    async def _is_verification_page(self) -> bool:
        """
        检查是否在验证码页面

        Returns:
            是否需要验证码
        """
        try:
            current_url = self.page.url

            # 检查 URL 是否是验证码页面
            for indicator in self.VERIFICATION_PAGE_INDICATORS:
                if indicator in current_url:
                    return True

            # 检查页面文本
            page_content = await self.page.content()
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

    async def _is_error_page(self) -> bool:
        """
        检查是否在错误页面（被检测到自动化）

        Returns:
            是否在错误页面
        """
        try:
            current_url = self.page.url
            page_content = await self.page.content()

            # 检查 URL 和页面内容
            for indicator in self.ERROR_PAGE_INDICATORS:
                if indicator in current_url or indicator in page_content:
                    print(f"  [!] 检测到错误页面: {indicator}")
                    return True

            return False
        except:
            return False

    async def _handle_error_page(self) -> bool:
        """
        处理错误页面（点击返回按钮重试）

        Returns:
            是否成功处理
        """
        try:
            print("  [错误] 尝试从错误页面恢复...")

            # 查找"注册或登录"按钮
            retry_selectors = [
                'button:has-text("注册或登录")',
                'button:has-text("Sign in")',
                'button:has-text("登录")',
                'a:has-text("注册或登录")',
                'a:has-text("Sign in")',
            ]

            for selector in retry_selectors:
                try:
                    btn = await self.page.query_selector(selector)
                    if btn and await btn.is_visible():
                        await self.human.wait_random(1000, 2000)
                        await self.human.human_click(btn)
                        print(f"  [错误] 已点击返回按钮")
                        await self.human.wait_random(2000, 4000)
                        return True
                except:
                    continue

            # 尝试返回上一页
            print("  [错误] 未找到返回按钮，尝试返回上一页...")
            await self.page.go_back()
            await self.human.wait_random(2000, 4000)
            return True

        except Exception as e:
            print(f"  [错误] 处理错误页面失败: {e}")
            return False

    async def _handle_verification(self, request_time: datetime, timeout: int = 120, target_email: str = None) -> bool:
        """
        处理验证码验证

        Args:
            request_time: 请求验证码的时间
            timeout: 超时时间
            target_email: 目标 Google 邮箱（用于精确匹配）

        Returns:
            是否成功
        """
        print("  [验证] 进入验证码处理流程...")

        # 如果配置了打码服务，启动验证码拦截器
        if self.captcha_service:
            print("  [验证] 已配置 YesCaptcha 打码服务，启动网络监控...")
            self._captcha_interceptor = CaptchaInterceptor(self.page, self.captcha_service)
            await self._captcha_interceptor.start_monitoring()

        try:
            # 等待页面显示"验证码已发送"提示，这表示验证码已经真正发送
            print("  [验证] 等待验证码发送提示 (最多60秒)...")

            # 如果有拦截器，使用拦截器的等待方法（可以检测 CAPTCHA 拦截）
            if self._captcha_interceptor:
                # 并行等待：验证码发送成功 或 CAPTCHA 处理完成
                sent_detected = await self._captcha_interceptor.wait_for_code_sent(timeout=60)
                if sent_detected:
                    print("  [验证] [网络监控] 检测到验证码发送成功!")
                else:
                    # 检查是否是 CAPTCHA 处理后成功
                    if self._captcha_interceptor.is_code_sent():
                        print("  [验证] [网络监控] CAPTCHA 处理后验证码发送成功!")
                        sent_detected = True
                    else:
                        print("  [验证] 网络监控未检测到发送成功，使用保底方法...")
                        sent_detected = await self._wait_for_resend_button(timeout=30)
            else:
                sent_detected = await self._wait_for_resend_button(timeout=60)

            if sent_detected:
                print("  [验证] 检测到验证码已发送提示!")
            else:
                print("  [验证] 未检测到发送提示，但仍尝试获取验证码...")

            # 更新请求时间为当前时间（因为验证码刚刚发送）
            actual_request_time = datetime.now()
            print(f"  [验证] 验证码请求时间: {actual_request_time.strftime('%H:%M:%S')}")

            # 使用保存的邮箱或传入的参数
            email_to_match = self._current_email or target_email
            if email_to_match:
                print(f"  [验证] 目标邮箱: {email_to_match}")
            else:
                print(f"  [验证] [警告] 未指定目标邮箱，将匹配任意验证码!")

            # 从邮箱获取验证码（异步调用，不阻塞事件循环）
            print(f"  [验证] 开始从 QQ 邮箱获取验证码 (超时={timeout}秒)...")
            code = await self.email_service.fetch_verification_code(
                timeout=timeout,
                since_time=actual_request_time,
                target_email=email_to_match
            )

            if not code:
                print("  [验证] [!] 未能获取到验证码!")
                return False

            print(f"  [验证] 成功获取验证码: {code}")

            # 输入验证码
            print(f"  [验证] 正在输入验证码到页面...")
            return await self._enter_verification_code(code)

        finally:
            # 停止拦截器
            if self._captcha_interceptor:
                self._captcha_interceptor.stop_monitoring()
                self._captcha_interceptor = None

    async def _wait_for_resend_button(self, timeout: int = 30) -> bool:
        """
        等待验证码发送完成的提示出现

        检测页面底部出现"验证码已发送，请查收你的邮件"消息，
        这表示验证码邮件已经真正发送。

        Args:
            timeout: 超时时间（秒）

        Returns:
            是否检测到发送成功提示
        """
        # 验证码已发送的提示文本
        sent_indicators = [
            # 中文
            "验证码已发送",
            "请查收你的邮件",
            "请查收您的邮件",
            "已发送验证码",
            "代码已发送",
            # 英文
            "Code sent",
            "code has been sent",
            "Check your email",
            "check your inbox",
        ]

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # 获取页面文本内容
                page_content = await self.page.content()

                # 检查是否包含发送成功的提示
                for indicator in sent_indicators:
                    if indicator.lower() in page_content.lower():
                        print(f"  检测到: {indicator}")
                        return True

                # 也检查 snackbar/toast 消息元素
                toast_selectors = [
                    '.mat-snack-bar-container',
                    '.snackbar',
                    '.toast',
                    '[role="alert"]',
                    '.mdc-snackbar',
                    '.notification',
                ]

                for selector in toast_selectors:
                    try:
                        elem = await self.page.query_selector(selector)
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

            # 检查是否还在验证码页面
            if not await self._is_verification_page():
                print(f"  页面已跳转，停止等待")
                return False

            await asyncio.sleep(0.5)

        print(f"  等待发送提示超时 ({timeout}秒)，尝试继续...")
        return False

    async def _enter_verification_code(self, code: str) -> bool:
        """
        输入验证码

        Args:
            code: 6位验证码（字母数字混合）

        Returns:
            是否成功
        """
        try:
            current_url = self.page.url
            print(f"  验证码页面: {current_url[:60]}...")

            # Gemini Business 验证码页面（6个独立输入框）
            if "accountverification.business.gemini.google" in current_url:
                return await self._enter_verification_code_gemini(code)
            else:
                # Google 标准验证码页面
                return await self._enter_verification_code_google(code)

        except Exception as e:
            print(f"  [!] 输入验证码出错: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def _enter_verification_code_gemini(self, code: str) -> bool:
        """
        在 Gemini Business 验证码页面输入验证码（6个独立输入框）

        Args:
            code: 6位验证码

        Returns:
            是否成功
        """
        try:
            print(f"  在 Gemini 验证码页面输入: {code}")

            # 等待输入框出现
            await asyncio.sleep(1)

            # 获取所有文本输入框
            inputs = await self.page.query_selector_all('input[type="text"]')
            visible_inputs = []
            for inp in inputs:
                if await inp.is_visible():
                    visible_inputs.append(inp)

            print(f"  找到 {len(visible_inputs)} 个输入框")

            if len(visible_inputs) >= 6:
                # 逐个输入每个字符
                for i, char in enumerate(code[:6]):
                    await visible_inputs[i].fill(char)
                    await asyncio.sleep(0.1)
                print(f"  已输入验证码: {code}")
            elif len(visible_inputs) == 1:
                # 只有一个输入框，直接输入整个验证码
                await visible_inputs[0].fill(code)
                print(f"  已输入验证码: {code}")
            else:
                print(f"  [!] 输入框数量异常: {len(visible_inputs)}")
                return False

            await asyncio.sleep(0.5)

            # 点击验证按钮
            verify_button = await self.page.query_selector('button:has-text("验证")')
            if not verify_button:
                verify_button = await self.page.query_selector('button[type="submit"]')

            if verify_button and await verify_button.is_visible():
                await verify_button.click()
                print("  已点击验证按钮")
            else:
                # 尝试按回车
                if visible_inputs:
                    await visible_inputs[-1].press("Enter")
                    print("  已按回车")

            # 等待响应
            await asyncio.sleep(3)

            return True

        except Exception as e:
            print(f"  [!] Gemini 验证码输入出错: {e}")
            return False

    async def _enter_verification_code_google(self, code: str) -> bool:
        """
        在 Google 标准验证码页面输入验证码

        Args:
            code: 6位验证码

        Returns:
            是否成功
        """
        try:
            print(f"  在 Google 验证码页面输入: {code}")

            # 尝试多个可能的输入框选择器
            for selector in self.SELECTORS["verification_inputs"]:
                try:
                    input_elem = await self.page.query_selector(selector)
                    if input_elem and await input_elem.is_visible():
                        await input_elem.fill("")
                        await asyncio.sleep(0.3)
                        await input_elem.fill(code)
                        await asyncio.sleep(0.5)
                        print(f"  已输入验证码: {code}")

                        # 尝试找到并点击下一步/验证按钮
                        for btn_selector in self.SELECTORS["verification_next_buttons"]:
                            try:
                                btn = await self.page.query_selector(btn_selector)
                                if btn and await btn.is_visible():
                                    await btn.click()
                                    print("  已点击验证按钮")
                                    break
                            except:
                                continue
                        else:
                            # 尝试按回车
                            await input_elem.press("Enter")
                            print("  已按回车")

                        # 等待响应
                        await asyncio.sleep(3)
                        return True
                except:
                    continue

            print("  [!] 未找到验证码输入框")
            return False

        except Exception as e:
            print(f"  [!] Google 验证码输入出错: {e}")
            return False

    async def _wait_for_login_success(self, timeout: int = 30) -> bool:
        """
        等待登录成功

        Args:
            timeout: 超时时间（秒）

        Returns:
            是否成功
        """
        print("  等待登录完成...")

        trial_signup_handled = False  # 标记是否已处理过注册页面

        for i in range(timeout):
            await asyncio.sleep(1)
            current_url = self.page.url

            # 检查是否登录成功
            for indicator in self.LOGIN_SUCCESS_INDICATORS:
                if indicator in current_url:
                    print("  登录成功!")
                    return True

            # 检查是否在首次注册页面（只处理一次）
            if not trial_signup_handled and await self._is_trial_signup_page():
                print("  检测到首次使用注册页面...")
                # 使用存储的邮箱前缀作为显示名称
                display_name = self._current_email.split("@")[0] if self._current_email else "Gemini User"
                if await self._handle_trial_signup(display_name):
                    # 注册成功，标记为已处理，等待页面跳转
                    trial_signup_handled = True
                    print("  等待页面跳转...")
                    # 额外等待几秒让页面跳转
                    await asyncio.sleep(3)
                    continue
                else:
                    return False

        print("  [!] 等待登录成功超时")
        return False

    async def _is_trial_signup_page(self) -> bool:
        """
        检查是否在首次使用注册页面

        Returns:
            是否在注册页面
        """
        try:
            current_url = self.page.url
            for indicator in self.TRIAL_SIGNUP_INDICATORS:
                if indicator in current_url:
                    return True
            return False
        except:
            return False

    async def _handle_trial_signup(self, display_name: str = None) -> bool:
        """
        处理首次使用的试用注册页面

        Args:
            display_name: 显示名称，如果不提供则使用默认值

        Returns:
            是否成功
        """
        try:
            print("  正在处理 Gemini Business 试用注册...")

            # 等待页面加载
            await asyncio.sleep(1)

            # 生成显示名称（如果未提供）
            if not display_name:
                # 从当前登录邮箱提取或使用默认值
                display_name = "Gemini User"

            # 查找姓名输入框（多种可能的选择器）
            name_input_selectors = [
                'input[type="text"]',           # 通用文本输入框
                'input[name="name"]',           # name 属性
                'input[placeholder*="名"]',     # 包含"名"的占位符
                'input[aria-label*="名"]',      # aria-label 包含"名"
            ]

            name_input = None
            for selector in name_input_selectors:
                try:
                    inputs = await self.page.query_selector_all(selector)
                    for inp in inputs:
                        if await inp.is_visible():
                            name_input = inp
                            break
                    if name_input:
                        break
                except:
                    continue

            if name_input:
                # 清空并输入名称
                await name_input.fill("")
                await asyncio.sleep(0.2)
                await name_input.fill(display_name)
                print(f"  已填写名称: {display_name}")
                await asyncio.sleep(0.3)
            else:
                print("  [!] 未找到名称输入框，尝试继续...")

            # 查找并点击"同意并开始使用"按钮
            submit_button_selectors = [
                'button:has-text("同意并开始使用")',
                'button:has-text("同意")',
                'button:has-text("开始")',
                'button:has-text("Start")',
                'button:has-text("Agree")',
                'button[type="submit"]',
            ]

            submit_button = None
            for selector in submit_button_selectors:
                try:
                    btn = await self.page.query_selector(selector)
                    if btn and await btn.is_visible():
                        submit_button = btn
                        break
                except:
                    continue

            if submit_button:
                await submit_button.click()
                print("  已点击同意按钮")
            else:
                # 尝试按回车
                if name_input:
                    await name_input.press("Enter")
                    print("  已按回车提交")
                else:
                    print("  [!] 未找到提交按钮")
                    return False

            # 智能等待页面跳转：持续等待直到离开注册页面
            print("  等待页面跳转...")
            max_wait = 120  # 最长等待2分钟

            for i in range(max_wait):
                await asyncio.sleep(1)
                current_url = self.page.url

                # 检查是否已离开注册页面
                if "admin/create" not in current_url:
                    if any(indicator in current_url for indicator in self.LOGIN_SUCCESS_INDICATORS):
                        print("  注册成功，已进入 Gemini Business")
                        return True
                    elif "business.gemini.google" in current_url:
                        print("  注册成功，页面已跳转")
                        return True
                    else:
                        # 跳转到其他页面
                        print(f"  页面已跳转: {current_url[:50]}...")
                        return True

                # 检测页面是否有变化或正在加载
                is_loading = await self._is_page_loading()

                # 每10秒报告一次状态
                if i > 0 and i % 10 == 0:
                    status = "页面加载中..." if is_loading else "等待跳转..."
                    print(f"  {status} ({i}秒)")

            # 超时
            print(f"  [!] 等待页面跳转超时 ({max_wait}秒)")
            return False

        except Exception as e:
            print(f"  [!] 处理注册页面出错: {e}")
            return False

    async def _is_page_loading(self) -> bool:
        """
        检测页面是否正在加载

        Returns:
            是否正在加载
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
                    elem = await self.page.query_selector(selector)
                    if elem and await elem.is_visible():
                        return True
                except:
                    continue

            # 检测按钮是否被禁用（表示正在处理）
            disabled_button = await self.page.query_selector('button[disabled]')
            if disabled_button and await disabled_button.is_visible():
                return True

            return False
        except:
            return False


async def _safe_goto(page, url: str, max_retries: int = 3, **kwargs) -> bool:
    """
    安全的页面导航，自动重试网络错误

    Args:
        page: Playwright 页面对象
        url: 目标 URL
        max_retries: 最大重试次数
        **kwargs: 传递给 page.goto 的其他参数

    Returns:
        是否成功
    """
    last_error = None
    for retry in range(max_retries):
        try:
            await page.goto(url, **kwargs)
            return True
        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            # 网络相关错误，可以重试
            if any(err in error_str for err in [
                "err_network_changed",
                "err_connection_reset",
                "err_connection_closed",
                "err_internet_disconnected",
                "err_name_not_resolved",
                "timeout",
                "net::",
            ]):
                if retry < max_retries - 1:
                    wait_time = (retry + 1) * 2  # 递增等待时间
                    print(f"    [重试] 页面导航失败，{wait_time}秒后重试 ({retry + 1}/{max_retries}): {str(e)[:50]}...")
                    await asyncio.sleep(wait_time)
                    continue
            # 非网络错误，不重试
            raise

    # 所有重试都失败
    if last_error:
        raise last_error
    return False


async def _dismiss_welcome_dialog(page) -> bool:
    """
    关闭 Gemini Business 的欢迎引导弹窗（"从您的数据中获取答案"）

    Args:
        page: Playwright 页面对象

    Returns:
        是否成功关闭（如果没有弹窗也返回 True）
    """
    try:
        # 等待页面稳定
        await asyncio.sleep(2)

        # 多种弹窗检测选择器
        dialog_selectors = [
            'div[role="dialog"]',
            '[role="dialog"]',
            '.mdc-dialog',
            '.mat-dialog-container',
            'div[class*="dialog"]',
            'div[class*="modal"]',
            # 根据弹窗内容检测
            'div:has-text("从您的数据中获取答案")',
            'div:has-text("关联您的数据")',
        ]

        dialog = None
        for selector in dialog_selectors:
            try:
                elem = await page.query_selector(selector)
                if elem and await elem.is_visible():
                    dialog = elem
                    print(f"  检测到弹窗 (选择器: {selector[:30]}...)")
                    break
            except:
                continue

        if not dialog:
            # 没有检测到弹窗，尝试直接查找关闭按钮
            pass

        # 尝试多种方式关闭弹窗
        dismiss_selectors = [
            # "以后再执行此操作" 按钮 - 各种可能的选择器
            'button:has-text("以后再执行此操作")',
            'button:has-text("以后再")',
            'button:has-text("稍后")',
            'button:has-text("跳过")',
            'button:has-text("取消")',
            'button:has-text("Skip")',
            'button:has-text("Later")',
            'button:has-text("Cancel")',
            'button:has-text("Not now")',
            'button:has-text("Maybe later")',
            # 根据按钮样式（非主要按钮通常是跳过）
            'button.mdc-button--outlined',
            'button[class*="secondary"]',
            'button[class*="text-button"]',
            # 弹窗内的第一个按钮
            'div[role="dialog"] button:first-of-type',
            '.mdc-dialog button:first-of-type',
            '.mat-dialog-actions button:first-of-type',
        ]

        for selector in dismiss_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    try:
                        btn_text = await btn.inner_text()
                        btn_text = btn_text.strip()[:30]
                    except:
                        btn_text = "(无法获取文本)"

                    print(f"  找到按钮: {btn_text}")
                    await btn.click()
                    await asyncio.sleep(2)
                    print(f"  已点击关闭按钮")
                    return True
            except Exception as e:
                continue

        # 尝试按 Escape 键关闭
        try:
            print(f"  尝试按 Escape 关闭...")
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)
        except:
            pass

        return True  # 即使没关闭也继续

    except Exception as e:
        print(f"  [DEBUG] 关闭弹窗时出错: {e}")
        return True


async def _handle_trial_signup_page(page, display_name: str = "Gemini User") -> bool:
    """
    处理首次使用的试用注册页面（独立函数版本）

    Args:
        page: Playwright 页面对象
        display_name: 显示名称

    Returns:
        是否成功
    """
    try:
        print("  正在处理 Gemini Business 试用注册...")

        # 等待页面加载
        await asyncio.sleep(1)

        # 查找姓名输入框（多种可能的选择器）
        name_input_selectors = [
            'input[type="text"]',           # 通用文本输入框
            'input[name="name"]',           # name 属性
            'input[placeholder*="名"]',     # 包含"名"的占位符
            'input[aria-label*="名"]',      # aria-label 包含"名"
        ]

        name_input = None
        for selector in name_input_selectors:
            try:
                inputs = await page.query_selector_all(selector)
                for inp in inputs:
                    if await inp.is_visible():
                        name_input = inp
                        break
                if name_input:
                    break
            except:
                continue

        if name_input:
            # 清空并输入名称
            await name_input.fill("")
            await asyncio.sleep(0.2)
            await name_input.fill(display_name)
            print(f"  已填写名称: {display_name}")
            await asyncio.sleep(0.3)
        else:
            print("  [!] 未找到名称输入框，尝试继续...")

        # 查找并点击"同意并开始使用"按钮
        submit_button_selectors = [
            'button:has-text("同意并开始使用")',
            'button:has-text("同意")',
            'button:has-text("开始")',
            'button:has-text("Start")',
            'button:has-text("Agree")',
            'button[type="submit"]',
        ]

        submit_button = None
        for selector in submit_button_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    submit_button = btn
                    break
            except:
                continue

        if submit_button:
            await submit_button.click()
            print("  已点击同意按钮")
        else:
            # 尝试按回车
            if name_input:
                await name_input.press("Enter")
                print("  已按回车提交")
            else:
                print("  [!] 未找到提交按钮")
                return False

        # 智能等待页面跳转：持续等待直到离开注册页面
        print("  等待页面跳转...")
        success_indicators = [
            "business.gemini.google/home",
            "business.gemini.google/cid",
        ]
        max_wait = 120  # 最长等待2分钟

        for i in range(max_wait):
            await asyncio.sleep(1)
            current_url = page.url

            # 检查是否已离开注册页面
            if "admin/create" not in current_url:
                if any(indicator in current_url for indicator in success_indicators):
                    print("  注册成功，已进入 Gemini Business")
                    return True
                elif "business.gemini.google" in current_url:
                    print("  注册成功，页面已跳转")
                    return True
                else:
                    print(f"  页面已跳转: {current_url[:50]}...")
                    return True

            # 每10秒报告一次状态
            if i > 0 and i % 10 == 0:
                print(f"  等待跳转... ({i}秒)")

        # 超时
        print(f"  [!] 等待页面跳转超时 ({max_wait}秒)")
        return False

    except Exception as e:
        print(f"  [!] 处理注册页面出错: {e}")
        return False


class AutoLoginService:
    """
    自动登录服务

    封装 Playwright 浏览器操作，提供凭证刷新功能
    """

    def __init__(self, config: dict):
        """
        初始化服务

        Args:
            config: 自动登录配置
        """
        self.config = config
        self.qq_email_config = config.get("qq_email", {})
        self.verification_timeout = config.get("verification_timeout", 120)
        self.headless = config.get("headless", True)  # 默认无头模式
        self._playwright = None
        self._browser = None
        # 不再使用共享的 context，每个账号独立

        # 初始化 YesCaptcha 打码服务（如果配置了 API key）
        yescaptcha_api_key = config.get("yescaptcha_api_key", "")
        if yescaptcha_api_key:
            self._captcha_service = YesCaptchaService(yescaptcha_api_key)
            print(f"  [YesCaptcha] 打码服务已初始化")
        else:
            self._captcha_service = None

    async def _ensure_browser(self):
        """确保浏览器已启动（使用反检测配置）"""
        if self._browser is None:
            try:
                print(f"  [浏览器] 正在启动 Playwright (反检测模式)...")
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()

                print(f"  [浏览器] 正在启动 Chromium (headless={self.headless})...")

                # 浏览器启动参数（反检测）
                launch_args = [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-infobars",
                    "--window-size=1920,1080",
                ]

                launch_options = {
                    "headless": self.headless,
                    "args": launch_args
                }

                # 如果配置了代理，添加到启动参数
                proxy_url = self.config.get("proxy")
                if proxy_url:
                    launch_options["proxy"] = {"server": proxy_url}
                    print(f"  [浏览器] 使用代理: {proxy_url}")

                self._browser = await self._playwright.chromium.launch(**launch_options)

                print(f"  [浏览器] 浏览器启动成功 (反检测模式)!")

            except ImportError:
                print(f"  [浏览器] [!] Playwright 未安装!")
                raise ImportError("请安装 playwright: pip install playwright && playwright install chromium")
            except Exception as e:
                print(f"  [浏览器] [!] 浏览器启动失败: {e}")
                raise

    async def _create_stealth_context(self):
        """为每个账号创建独立的反检测浏览器上下文"""
        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        # 注入反检测脚本
        await context.add_init_script("""
            // 隐藏 webdriver 属性
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

            // 修改 plugins 数组
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });

            // 修改 languages
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });

            // 覆盖 chrome 对象
            window.chrome = { runtime: {} };

            // 修改 permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)

        return context

    async def close(self):
        """关闭浏览器"""
        if self._captcha_service:
            await self._captcha_service.close()
            self._captcha_service = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def refresh_account(
        self,
        account: AccountConfig,
        google_email: str
    ) -> Optional[Dict[str, Any]]:
        """
        刷新账号凭证

        Args:
            account: 账号配置
            google_email: Google 邮箱

        Returns:
            新凭证字典，失败返回 None
        """
        from .email_service import EmailVerificationService

        await self._ensure_browser()

        # 创建邮件服务
        email_service = EmailVerificationService(self.qq_email_config)
        if not await email_service.connect():
            print("  [!] 无法连接到邮箱")
            return None

        page = None
        context = None

        try:
            # 为每个账号创建独立的浏览器上下文（隔离 cookie）
            context = await self._create_stealth_context()
            page = await context.new_page()

            # 注入额外的反检测脚本
            await inject_stealth_scripts(page)

            # 构造目标 URL
            target_url = f"https://business.gemini.google/home/cid/{account.team_id}"
            if account.csesidx:
                target_url += f"?csesidx={account.csesidx}"

            print(f"  正在访问: {target_url[:60]}...")
            await _safe_goto(page, target_url, wait_until="networkidle", timeout=60000)

            # 随机等待，模拟人类行为
            await asyncio.sleep(random.uniform(2, 4))

            current_url = page.url
            print(f"  当前URL: {current_url[:80]}...")

            # 首先检查是否在首次注册页面
            if "business.gemini.google/admin/create" in current_url:
                print("  检测到首次使用注册页面...")
                display_name = account.note or google_email.split("@")[0]
                if not await _handle_trial_signup_page(page, display_name):
                    return None
                await asyncio.sleep(3)
                current_url = page.url

            # 检查是否需要登录
            need_login = (
                "accounts.google.com" in current_url or
                "auth.business.gemini.google" in current_url or
                "signin" in current_url.lower()
            )

            if need_login:
                print(f"  需要登录 Google 账号...")

                auto_login = GoogleAutoLogin(page, email_service, captcha_service=self._captcha_service)
                login_success = await auto_login.login(
                    google_email=google_email,
                    verification_timeout=self.verification_timeout
                )

                if not login_success:
                    print("  [!] 自动登录失败")
                    return None

                # 重新访问目标页面
                print(f"  重新访问目标页面...")
                await _safe_goto(page, target_url, wait_until="networkidle", timeout=60000)
                await asyncio.sleep(3)
                current_url = page.url
                print(f"  当前URL: {current_url[:80]}...")

            # 处理首次注册页面
            if "business.gemini.google/admin/create" in current_url:
                display_name = account.note or google_email.split("@")[0]
                if not await _handle_trial_signup_page(page, display_name):
                    return None
                await asyncio.sleep(3)
                current_url = page.url

            # 等待进入聊天页面
            if "business.gemini.google" in current_url and "/cid/" not in current_url:
                print(f"  等待进入聊天页面...")
                for _ in range(30):
                    await asyncio.sleep(1)
                    current_url = page.url
                    if "business.gemini.google/admin/create" in current_url:
                        display_name = account.note or google_email.split("@")[0]
                        await _handle_trial_signup_page(page, display_name)
                        continue
                    if "/cid/" in current_url:
                        break

            # 关闭欢迎弹窗
            await _dismiss_welcome_dialog(page)

            # 提取凭证
            current_url = page.url
            is_gemini_page = (
                "business.gemini.google" in current_url and
                "/cid/" in current_url and
                "auth.business.gemini.google" not in current_url
            )

            if not is_gemini_page:
                print(f"  [!] 未能进入聊天页面: {current_url[:80]}")
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
                print("  凭证提取成功")
                return credentials
            else:
                print("  [!] 未能获取 secure_c_ses cookie")
                return None

        except Exception as e:
            print(f"  [!] 刷新凭证时出错: {e}")
            import traceback
            traceback.print_exc()
            return None
        finally:
            await email_service.disconnect()
            if page:
                await page.close()
            # 每个账号都要关闭 context，确保下个账号是全新的
            if context:
                await context.close()

    async def register_new_account(
        self,
        google_email: str,
        note: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        注册新账号（从邮箱开始，完成登录和首次注册）

        Args:
            google_email: Google 邮箱地址
            note: 账号备注

        Returns:
            新凭证字典，失败返回 None
        """
        from .email_service import EmailVerificationService

        print(f"\n{'='*60}")
        print(f"  [注册] 开始注册新账号: {google_email}")
        print(f"{'='*60}")

        await self._ensure_browser()

        # 创建邮件服务
        print(f"  [注册] 正在连接 QQ 邮箱...")
        email_service = EmailVerificationService(self.qq_email_config)
        if not await email_service.connect():
            print(f"  [注册] [!] 无法连接到 QQ 邮箱!")
            return None

        context = None
        page = None

        try:
            print(f"  [注册] 创建浏览器页面...")

            # 为每个账号创建独立的浏览器上下文（隔离 cookie）
            context = await self._create_stealth_context()
            page = await context.new_page()

            # 注入额外的反检测脚本
            await inject_stealth_scripts(page)

            # 访问 Gemini Business 主页
            target_url = "https://business.gemini.google/"
            print(f"  [注册] 正在访问: {target_url}")
            await _safe_goto(page, target_url, wait_until="networkidle", timeout=60000)

            # 随机等待，模拟人类行为
            await asyncio.sleep(random.uniform(2, 4))

            current_url = page.url
            print(f"  [注册] 当前URL: {current_url}")

            # 首先检查是否在首次注册页面
            if "business.gemini.google/admin/create" in current_url:
                print(f"  [注册] 检测到首次使用注册页面...")
                display_name = note if note else google_email.split("@")[0]
                if not await _handle_trial_signup_page(page, display_name):
                    print(f"  [注册] [!] 首次注册失败")
                    return None
                await asyncio.sleep(3)
                current_url = page.url

            # 检查是否需要登录
            need_login = (
                "accounts.google.com" in current_url or
                "auth.business.gemini.google" in current_url or
                "signin" in current_url.lower() or
                "google.com/signin" in current_url
            )

            print(f"  [注册] 是否需要登录: {need_login}")

            if need_login:
                print(f"  [注册] 开始 Google 登录流程...")

                auto_login = GoogleAutoLogin(page, email_service, captcha_service=self._captcha_service)
                login_success = await auto_login.login(
                    google_email=google_email,
                    verification_timeout=self.verification_timeout
                )

                if not login_success:
                    print(f"  [注册] [!] 自动登录失败!")
                    return None

                await asyncio.sleep(3)
                current_url = page.url
                print(f"  [注册] 登录后URL: {current_url}")
            else:
                print(f"  [注册] 无需登录，已有会话")

            # 处理首次注册页面
            if "business.gemini.google/admin/create" in current_url:
                print(f"  [注册] 正在处理首次注册页面...")
                display_name = note if note else google_email.split("@")[0]
                if not await _handle_trial_signup_page(page, display_name):
                    print(f"  [注册] [!] 首次注册失败!")
                    return None
                await asyncio.sleep(3)
                current_url = page.url

            # 等待进入聊天页面
            print(f"  [注册] 等待进入聊天页面 (最多60秒)...")
            for i in range(60):
                await asyncio.sleep(1)
                current_url = page.url
                if "/cid/" in current_url or "/home/cid/" in current_url:
                    print(f"  [注册] 成功进入聊天页面!")
                    break
                # 处理可能出现的注册页面
                if "admin/create" in current_url:
                    display_name = note if note else google_email.split("@")[0]
                    await _handle_trial_signup_page(page, display_name)
                if i > 0 and i % 10 == 0:
                    print(f"  [注册] 等待中... [{i}/60] URL={current_url[:50]}...")

            # 关闭欢迎弹窗
            print(f"  [注册] 关闭欢迎弹窗...")
            await _dismiss_welcome_dialog(page)

            # 提取凭证
            current_url = page.url
            print(f"  [注册] 最终URL: {current_url}")

            if "business.gemini.google" not in current_url:
                print(f"  [注册] [!] 未能进入 Gemini Business!")
                return None

            print(f"  [注册] 正在提取凭证...")
            credentials = {
                "google_email": google_email,
                "note": note if note else google_email.split("@")[0]
            }

            # 提取 team_id
            cid_match = re.search(r'/cid/([^/?#]+)', current_url)
            if cid_match:
                credentials["team_id"] = cid_match.group(1)
                print(f"  [注册] team_id: {credentials['team_id'][:20]}...")

            # 提取 csesidx
            parsed = urlparse(current_url)
            params = parse_qs(parsed.query)
            if "csesidx" in params:
                credentials["csesidx"] = params["csesidx"][0]
                print(f"  [注册] csesidx: {credentials['csesidx'][:20]}...")

            # 提取 Cookies
            cookies = await context.cookies("https://business.gemini.google")
            for cookie in cookies:
                if cookie["name"] == "__Secure-C_SES":
                    credentials["secure_c_ses"] = cookie["value"]
                    print(f"  [注册] secure_c_ses: {cookie['value'][:20]}...")
                elif cookie["name"] == "__Host-C_OSES":
                    credentials["host_c_oses"] = cookie["value"]
                    print(f"  [注册] host_c_oses: {cookie['value'][:20]}...")

            if credentials.get("secure_c_ses") and credentials.get("team_id"):
                credentials["refresh_time"] = datetime.now().isoformat()
                print(f"  [注册] 账号注册成功!")
                print(f"{'='*60}\n")
                return credentials
            else:
                missing = []
                if not credentials.get("secure_c_ses"):
                    missing.append("secure_c_ses")
                if not credentials.get("team_id"):
                    missing.append("team_id")
                print(f"  [注册] [!] 缺少必要凭证: {', '.join(missing)}")
                return None

        except Exception as e:
            print(f"  [注册] [!] 注册账号时出错: {e}")
            import traceback
            traceback.print_exc()
            return None
        finally:
            await email_service.disconnect()
            if page:
                await page.close()
            # 每个账号都要关闭 context，确保下个账号是全新的
            if context:
                await context.close()
