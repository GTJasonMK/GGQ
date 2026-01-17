"""
浏览器工具函数

提供 Playwright 相关的工具函数
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def safe_goto(page, url: str, max_retries: int = 3, **kwargs) -> bool:
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
                "timeout",
                "net::",
            ]):
                if retry < max_retries - 1:
                    wait_time = (retry + 1) * 2
                    logger.warning(f"页面导航失败，{wait_time}秒后重试 ({retry + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                    continue
            raise

    if last_error:
        raise last_error
    return False


async def is_page_loading(page) -> bool:
    """
    检测页面是否正在加载

    检测以下加载指示器：
    - 进度条元素
    - 加载动画元素
    - 禁用状态的按钮

    Args:
        page: Playwright 页面对象

    Returns:
        True 如果页面正在加载，False 否则
    """
    # 加载指示器选择器
    loading_selectors = [
        '[role="progressbar"]',
        '.loading',
        '.spinner',
        '.mat-progress-spinner',
        '.mdc-circular-progress',
        '.loading-indicator',
        '[aria-busy="true"]',
        '.skeleton',
        '.shimmer',
    ]

    # 检查加载元素
    for selector in loading_selectors:
        try:
            elem = await page.query_selector(selector)
            if elem and await elem.is_visible():
                return True
        except:
            continue

    # 检查是否有禁用状态的主要按钮（通常在加载时禁用）
    disabled_button_selectors = [
        'button[disabled]:has-text("提交")',
        'button[disabled]:has-text("Submit")',
        'button[disabled]:has-text("继续")',
        'button[disabled]:has-text("Continue")',
    ]

    for selector in disabled_button_selectors:
        try:
            elem = await page.query_selector(selector)
            if elem and await elem.is_visible():
                return True
        except:
            continue

    return False


async def wait_for_page_ready(page, timeout: int = 30) -> bool:
    """
    等待页面加载完成

    Args:
        page: Playwright 页面对象
        timeout: 超时时间（秒）

    Returns:
        True 如果页面加载完成，False 如果超时
    """
    for _ in range(timeout * 2):
        if not await is_page_loading(page):
            return True
        await asyncio.sleep(0.5)

    return False


async def dismiss_welcome_dialog(page, max_attempts: int = 3) -> bool:
    """
    关闭 Gemini Business 的欢迎引导弹窗

    支持多种弹窗类型：
    - 首次使用引导
    - 功能介绍弹窗
    - 数据关联弹窗

    Args:
        page: Playwright 页面对象
        max_attempts: 最大尝试次数（可能有多个连续弹窗）

    Returns:
        是否成功关闭
    """
    for attempt in range(max_attempts):
        try:
            await asyncio.sleep(2)

            # 多种弹窗检测选择器
            dialog_selectors = [
                'div[role="dialog"]',
                '.mdc-dialog',
                '.mat-dialog-container',
                '.cdk-overlay-pane',
                '.modal',
                '[aria-modal="true"]',
            ]

            # 弹窗内容检测（用于确认是否有需要关闭的弹窗）
            content_indicators = [
                "从您的数据中获取答案",
                "关联您的数据",
                "欢迎",
                "Welcome",
                "Get started",
                "开始使用",
                "Connect your data",
            ]

            has_dialog = False
            for selector in dialog_selectors:
                try:
                    elem = await page.query_selector(selector)
                    if elem and await elem.is_visible():
                        has_dialog = True
                        # 检查弹窗内容
                        dialog_text = await elem.inner_text()
                        for indicator in content_indicators:
                            if indicator in dialog_text:
                                logger.debug(f"检测到弹窗: {indicator}")
                                break
                        break
                except:
                    continue

            if not has_dialog:
                # 尝试通过内容检测弹窗
                try:
                    page_content = await page.content()
                    for indicator in content_indicators:
                        if indicator in page_content:
                            has_dialog = True
                            break
                except:
                    pass

            if not has_dialog and attempt > 0:
                logger.debug("未检测到更多弹窗")
                return True

            # 尝试多种方式关闭弹窗（按优先级排序）
            dismiss_selectors = [
                # 中文选项
                'button:has-text("以后再执行此操作")',
                'button:has-text("稍后")',
                'button:has-text("跳过")',
                'button:has-text("取消")',
                'button:has-text("关闭")',
                'button:has-text("不了，谢谢")',
                # 英文选项
                'button:has-text("Maybe later")',
                'button:has-text("Skip")',
                'button:has-text("Later")',
                'button:has-text("Cancel")',
                'button:has-text("Close")',
                'button:has-text("No thanks")',
                'button:has-text("Not now")',
                # 通用选项
                'button.mdc-button--outlined',
                'button[aria-label="Close"]',
                'button[aria-label="关闭"]',
                '.mdc-dialog__button--cancel',
                '[data-dismiss="modal"]',
            ]

            clicked = False
            for selector in dismiss_selectors:
                try:
                    btn = await page.query_selector(selector)
                    if btn and await btn.is_visible():
                        await btn.click()
                        logger.debug(f"点击关闭按钮: {selector}")
                        await asyncio.sleep(1)
                        clicked = True
                        break
                except:
                    continue

            if not clicked:
                # 尝试按 Escape 键关闭
                try:
                    await page.keyboard.press("Escape")
                    logger.debug("按 Escape 键关闭弹窗")
                    await asyncio.sleep(1)
                except:
                    pass

            # 如果是第一次尝试且没有点击任何按钮，直接返回
            if attempt == 0 and not clicked and not has_dialog:
                return True

        except Exception as e:
            logger.debug(f"关闭弹窗时出错: {e}")

    return True


async def handle_trial_signup(page, display_name: str = "Gemini User") -> bool:
    """
    处理首次使用的试用注册页面

    Args:
        page: Playwright 页面对象
        display_name: 显示名称

    Returns:
        是否成功
    """
    try:
        logger.info("正在处理 Gemini Business 试用注册...")

        # 等待页面加载完成
        await wait_for_page_ready(page, timeout=10)
        await asyncio.sleep(1)

        # 查找姓名输入框
        name_input_selectors = [
            'input[type="text"]',
            'input[name="name"]',
            'input[placeholder*="名称"]',
            'input[placeholder*="name"]',
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
            await name_input.fill("")
            await asyncio.sleep(0.2)
            await name_input.fill(display_name)
            logger.info(f"已填写名称: {display_name}")
            await asyncio.sleep(0.3)

        # 查找并点击提交按钮
        submit_button_selectors = [
            'button:has-text("同意并开始使用")',
            'button:has-text("同意")',
            'button:has-text("开始使用")',
            'button:has-text("Start")',
            'button:has-text("Agree")',
            'button:has-text("Accept")',
            'button:has-text("Get started")',
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
            logger.info("已点击同意按钮")
        elif name_input:
            await name_input.press("Enter")
            logger.info("已按回车提交")
        else:
            logger.warning("未找到提交按钮")
            return False

        # 等待页面跳转（最多120秒）
        success_indicators = [
            "business.gemini.google/home",
            "business.gemini.google/cid",
        ]

        for i in range(120):
            await asyncio.sleep(1)
            current_url = page.url

            # 检查是否还在加载
            if await is_page_loading(page):
                if i > 0 and i % 10 == 0:
                    logger.debug(f"页面加载中... ({i}秒)")
                continue

            if "admin/create" not in current_url:
                if any(indicator in current_url for indicator in success_indicators):
                    logger.info("注册成功")
                    return True
                elif "business.gemini.google" in current_url:
                    logger.info("已进入 Gemini Business 页面")
                    return True

            if i > 0 and i % 10 == 0:
                logger.debug(f"等待跳转... ({i}秒)")

        logger.warning("等待页面跳转超时")
        return False

    except Exception as e:
        logger.error(f"处理注册页面出错: {e}")
        return False
