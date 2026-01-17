"""
JWT Token Utilities
"""
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional

from jose import jwt, JWTError

from app.config import (
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS
)


def create_access_token(user_id: int, role: int, username: str = "", expires_delta: Optional[timedelta] = None) -> str:
    """创建访问令牌"""
    if expires_delta is None:
        expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    expire = datetime.utcnow() + expires_delta

    payload = {
        "sub": str(user_id),
        "role": role,
        "username": username,
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "access"
    }

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token() -> tuple[str, str, datetime]:
    """
    创建刷新令牌
    返回: (明文token, token哈希, 过期时间)
    """
    # 生成64字节随机token
    token = secrets.token_urlsafe(64)

    # 计算哈希用于存储
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # 计算过期时间
    expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    return token, token_hash, expires_at


def hash_refresh_token(token: str) -> str:
    """计算刷新令牌的哈希"""
    return hashlib.sha256(token.encode()).hexdigest()


def decode_access_token(token: str) -> Optional[dict]:
    """
    解码访问令牌
    返回: payload字典 或 None(如果无效)
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])

        # 验证token类型
        if payload.get("type") != "access":
            return None

        return payload
    except JWTError:
        return None


def get_token_expire_seconds() -> int:
    """获取访问令牌过期秒数"""
    return ACCESS_TOKEN_EXPIRE_MINUTES * 60
