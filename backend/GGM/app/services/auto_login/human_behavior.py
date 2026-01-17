"""
人类行为模拟模块

模拟真实用户的鼠标移动、打字、点击等行为，规避自动化检测
"""
import asyncio
import random
import math
from typing import Tuple, Optional


class HumanBehavior:
    """
    人类行为模拟器

    提供模拟真实用户操作的方法，包括：
    - 随机延迟
    - 人类打字模拟
    - 鼠标移动轨迹
    - 自然点击
    """

    # 打字速度参数（毫秒）
    TYPING_SPEED = {
        "fast": (30, 80),      # 快速打字
        "normal": (50, 150),   # 正常速度
        "slow": (100, 250),    # 慢速打字
        "human": (40, 200),    # 人类真实速度（有变化）
    }

    # 常见打字错误（可选）
    TYPO_CHARS = {
        'a': ['s', 'q', 'z'],
        'b': ['v', 'n', 'g'],
        'c': ['x', 'v', 'd'],
        # ... 可以添加更多
    }

    def __init__(self, page):
        """
        初始化

        Args:
            page: Playwright 页面对象
        """
        self.page = page

    @staticmethod
    def random_delay(min_ms: int = 100, max_ms: int = 500) -> float:
        """
        生成随机延迟时间（使用正态分布更接近人类行为）

        Args:
            min_ms: 最小延迟（毫秒）
            max_ms: 最大延迟（毫秒）

        Returns:
            延迟时间（秒）
        """
        # 使用正态分布，中心值在中间
        mean = (min_ms + max_ms) / 2
        std = (max_ms - min_ms) / 4
        delay = random.gauss(mean, std)
        # 限制在范围内
        delay = max(min_ms, min(max_ms, delay))
        return delay / 1000

    async def wait_random(self, min_ms: int = 500, max_ms: int = 2000):
        """
        随机等待

        Args:
            min_ms: 最小等待时间（毫秒）
            max_ms: 最大等待时间（毫秒）
        """
        await asyncio.sleep(self.random_delay(min_ms, max_ms))

    async def type_like_human(
        self,
        element,
        text: str,
        speed: str = "human",
        clear_first: bool = True
    ):
        """
        模拟人类打字

        不使用 fill()，而是逐字符输入，模拟真实打字

        Args:
            element: 输入框元素
            text: 要输入的文本
            speed: 打字速度 ("fast", "normal", "slow", "human")
            clear_first: 是否先清空输入框
        """
        min_delay, max_delay = self.TYPING_SPEED.get(speed, self.TYPING_SPEED["human"])

        # 先聚焦元素
        await element.click()
        await asyncio.sleep(self.random_delay(100, 300))

        # 清空输入框（如果需要）
        if clear_first:
            # 选中所有内容
            await self.page.keyboard.press("Control+a")
            await asyncio.sleep(self.random_delay(50, 150))
            await self.page.keyboard.press("Backspace")
            await asyncio.sleep(self.random_delay(100, 300))

        # 逐字符输入
        for i, char in enumerate(text):
            # 打字间隔使用随机延迟
            if i > 0:
                # 人类打字有节奏变化，偶尔会快一点或慢一点
                if random.random() < 0.1:
                    # 10% 概率短暂停顿（思考）
                    await asyncio.sleep(self.random_delay(200, 500))
                else:
                    await asyncio.sleep(self.random_delay(min_delay, max_delay))

            # 输入字符
            await self.page.keyboard.type(char)

        # 打字完成后短暂停顿
        await asyncio.sleep(self.random_delay(200, 500))

    async def move_mouse_to_element(self, element, click: bool = False):
        """
        模拟人类鼠标移动到元素

        使用贝塞尔曲线模拟自然的鼠标移动轨迹

        Args:
            element: 目标元素
            click: 是否点击
        """
        # 获取元素边界框
        box = await element.bounding_box()
        if not box:
            if click:
                await element.click()
            return

        # 计算目标点（元素中心附近的随机位置）
        target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)

        # 移动鼠标
        await self.page.mouse.move(target_x, target_y)

        # 短暂停顿后点击
        if click:
            await asyncio.sleep(self.random_delay(50, 200))
            await self.page.mouse.click(target_x, target_y)

    async def human_click(self, element):
        """
        模拟人类点击

        先移动鼠标到元素，然后点击

        Args:
            element: 要点击的元素
        """
        await self.move_mouse_to_element(element, click=True)

    async def scroll_into_view(self, element):
        """
        将元素滚动到可视区域（模拟人类滚动）

        Args:
            element: 目标元素
        """
        await element.scroll_into_view_if_needed()
        await asyncio.sleep(self.random_delay(200, 500))

    async def random_mouse_movement(self, count: int = 2):
        """
        随机鼠标移动（模拟用户浏览页面）

        Args:
            count: 移动次数
        """
        viewport = self.page.viewport_size
        if not viewport:
            return

        for _ in range(count):
            x = random.randint(100, viewport["width"] - 100)
            y = random.randint(100, viewport["height"] - 100)
            await self.page.mouse.move(x, y)
            await asyncio.sleep(self.random_delay(100, 300))

    async def warm_up_session(self, duration: int = 5):
        """
        预热浏览器会话，提高 reCAPTCHA 评分

        通过模拟真实用户行为（滚动、移动鼠标、停顿）来
        提高 Google reCAPTCHA v3 的信任评分

        Args:
            duration: 预热持续时间（秒）
        """
        print(f"  [预热] 开始预热浏览器会话 ({duration}秒)...")
        start_time = asyncio.get_event_loop().time()
        viewport = self.page.viewport_size or {"width": 1280, "height": 800}

        actions_done = 0

        while asyncio.get_event_loop().time() - start_time < duration:
            action = random.choice(["scroll", "mouse", "wait", "scroll_up"])

            if action == "scroll":
                # 向下滚动
                scroll_amount = random.randint(100, 300)
                await self.page.mouse.wheel(0, scroll_amount)
                await asyncio.sleep(random.uniform(0.3, 0.8))

            elif action == "scroll_up":
                # 偶尔向上滚动
                scroll_amount = random.randint(-200, -50)
                await self.page.mouse.wheel(0, scroll_amount)
                await asyncio.sleep(random.uniform(0.3, 0.8))

            elif action == "mouse":
                # 随机移动鼠标（使用曲线轨迹）
                target_x = random.randint(100, viewport["width"] - 100)
                target_y = random.randint(100, viewport["height"] - 100)
                # 分多步移动，模拟自然轨迹
                steps = random.randint(10, 20)
                await self.page.mouse.move(target_x, target_y, steps=steps)
                await asyncio.sleep(random.uniform(0.2, 0.5))

            else:  # wait
                # 模拟阅读停顿
                await asyncio.sleep(random.uniform(0.5, 1.5))

            actions_done += 1

        print(f"  [预热] 预热完成，执行了 {actions_done} 个动作")

    async def simulate_reading(self, seconds: float = 3):
        """
        模拟用户阅读页面

        Args:
            seconds: 阅读时间
        """
        end_time = asyncio.get_event_loop().time() + seconds
        viewport = self.page.viewport_size or {"width": 1280, "height": 800}

        while asyncio.get_event_loop().time() < end_time:
            # 偶尔小幅移动鼠标（阅读时的自然抖动）
            if random.random() < 0.3:
                x = random.randint(200, viewport["width"] - 200)
                y = random.randint(200, viewport["height"] - 200)
                await self.page.mouse.move(x, y, steps=5)

            await asyncio.sleep(random.uniform(0.3, 0.8))


async def setup_stealth_browser(playwright, headless: bool = True, user_data_dir: str = None):
    """
    设置隐身浏览器，规避自动化检测

    Args:
        playwright: Playwright 实例
        headless: 是否无头模式（默认True）
        user_data_dir: 用户数据目录（持久化会话）

    Returns:
        browser, context 元组
    """
    # 浏览器启动参数
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--window-size=1920,1080",
        "--start-maximized",
        # 额外的反检测参数
        "--disable-extensions",
        "--disable-plugins-discovery",
        "--disable-background-networking",
    ]

    # 如果指定了用户数据目录，使用持久化上下文
    if user_data_dir:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir,
            headless=headless,
            args=launch_args,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        browser = None  # 持久化上下文没有独立的 browser
    else:
        browser = await playwright.chromium.launch(
            headless=headless,
            args=launch_args
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

    # 注入反检测脚本
    await context.add_init_script("""
        // 隐藏 webdriver 属性
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });

        // 修改 plugins 数组长度
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });

        // 修改 languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en']
        });

        // 覆盖 chrome 对象
        window.chrome = {
            runtime: {}
        };

        // 修改 permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );

        // 隐藏 automation 相关属性
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
    """)

    return browser, context


async def inject_stealth_scripts(page):
    """
    在页面上注入额外的反检测脚本

    Args:
        page: Playwright 页面对象
    """
    await page.add_init_script("""
        // 更多反检测措施

        // 修改 window.outerWidth/outerHeight
        Object.defineProperty(window, 'outerWidth', {
            get: () => window.innerWidth + 100
        });
        Object.defineProperty(window, 'outerHeight', {
            get: () => window.innerHeight + 100
        });

        // 伪造 WebGL 信息
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) {
                return 'Intel Inc.';
            }
            if (parameter === 37446) {
                return 'Intel Iris OpenGL Engine';
            }
            return getParameter.apply(this, arguments);
        };
    """)
