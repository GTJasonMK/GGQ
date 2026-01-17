"""
验证码服务
生成、存储和验证邮箱验证码
"""
import random
import string
import time
from typing import Optional, Tuple
from threading import Lock

from ..config import VERIFICATION_CODE_EXPIRE_MINUTES
from .email_service import email_service


class VerificationService:
    """验证码服务"""

    def __init__(self):
        # 内存存储验证码: {email: (code, expire_time, attempts)}
        self._codes: dict = {}
        self._lock = Lock()
        # 发送频率限制: {email: last_send_time}
        self._send_limits: dict = {}
        # 最小发送间隔（秒）
        self.MIN_SEND_INTERVAL = 60
        # 最大验证尝试次数
        self.MAX_VERIFY_ATTEMPTS = 5

    def _generate_code(self, length: int = 6) -> str:
        """生成随机验证码"""
        return ''.join(random.choices(string.digits, k=length))

    def _cleanup_expired(self):
        """清理过期验证码"""
        current_time = time.time()
        with self._lock:
            expired_emails = [
                email for email, (_, expire_time, _) in self._codes.items()
                if current_time > expire_time
            ]
            for email in expired_emails:
                del self._codes[email]

            # 清理过期的发送限制记录
            expired_limits = [
                email for email, send_time in self._send_limits.items()
                if current_time - send_time > 3600  # 1小时后清理
            ]
            for email in expired_limits:
                del self._send_limits[email]

    def can_send(self, email: str) -> Tuple[bool, Optional[str]]:
        """
        检查是否可以发送验证码

        Returns:
            (可以发送, 错误信息)
        """
        self._cleanup_expired()
        current_time = time.time()

        with self._lock:
            last_send_time = self._send_limits.get(email, 0)
            if current_time - last_send_time < self.MIN_SEND_INTERVAL:
                remaining = int(self.MIN_SEND_INTERVAL - (current_time - last_send_time))
                return False, f"发送过于频繁，请 {remaining} 秒后重试"

        return True, None

    def send_code(self, email: str) -> Tuple[bool, str]:
        """
        发送验证码

        Args:
            email: 收件人邮箱

        Returns:
            (是否成功, 消息)
        """
        # 检查发送频率
        can_send, error = self.can_send(email)
        if not can_send:
            return False, error

        # 生成验证码
        code = self._generate_code()
        expire_time = time.time() + VERIFICATION_CODE_EXPIRE_MINUTES * 60

        # 发送邮件
        if not email_service.send_verification_code(email, code, VERIFICATION_CODE_EXPIRE_MINUTES):
            return False, "验证码发送失败，请稍后重试"

        # 存储验证码
        with self._lock:
            self._codes[email] = (code, expire_time, 0)
            self._send_limits[email] = time.time()

        return True, "验证码已发送，请查收邮件"

    def verify_code(self, email: str, code: str) -> Tuple[bool, str]:
        """
        验证验证码

        Args:
            email: 邮箱
            code: 用户输入的验证码

        Returns:
            (是否验证成功, 消息)
        """
        self._cleanup_expired()
        current_time = time.time()

        with self._lock:
            if email not in self._codes:
                return False, "验证码不存在或已过期，请重新获取"

            stored_code, expire_time, attempts = self._codes[email]

            # 检查是否过期
            if current_time > expire_time:
                del self._codes[email]
                return False, "验证码已过期，请重新获取"

            # 检查尝试次数
            if attempts >= self.MAX_VERIFY_ATTEMPTS:
                del self._codes[email]
                return False, "验证次数过多，请重新获取验证码"

            # 验证码匹配
            if code == stored_code:
                del self._codes[email]  # 验证成功后删除
                return True, "验证成功"

            # 验证失败，增加尝试次数
            self._codes[email] = (stored_code, expire_time, attempts + 1)
            remaining = self.MAX_VERIFY_ATTEMPTS - attempts - 1
            return False, f"验证码错误，还剩 {remaining} 次尝试机会"

    def has_valid_code(self, email: str) -> bool:
        """检查邮箱是否有未过期的验证码"""
        self._cleanup_expired()
        with self._lock:
            if email not in self._codes:
                return False
            _, expire_time, _ = self._codes[email]
            return time.time() <= expire_time


# 创建单例实例
verification_service = VerificationService()
