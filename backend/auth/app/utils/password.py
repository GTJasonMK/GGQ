"""
Password Hashing Utilities
"""
from passlib.context import CryptContext

from app.config import BCRYPT_ROUNDS

# 创建密码上下文
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=BCRYPT_ROUNDS
)


def hash_password(password: str) -> str:
    """哈希密码"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def check_password_strength(password: str) -> tuple[bool, str]:
    """
    检查密码强度
    返回: (是否通过, 提示信息)
    """
    if len(password) < 8:
        return False, "密码长度至少8位"

    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)

    if not has_letter:
        return False, "密码必须包含字母"
    if not has_digit:
        return False, "密码必须包含数字"

    return True, "密码强度符合要求"
