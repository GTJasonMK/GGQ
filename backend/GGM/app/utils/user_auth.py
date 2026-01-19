"""
用户认证工具
- 验证 auth 服务的 JWT Token
- 获取用户信息
"""
import os
import sys
import logging
from typing import Optional
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

# 读取统一配置（与 auth 服务保持一致）
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    import config as unified_config
except ImportError:
    unified_config = None

logger = logging.getLogger(__name__)

# JWT 配置（与 auth 服务保持一致）
# 开发环境默认密钥，生产环境必须通过环境变量 AUTH_JWT_SECRET 设置
_DEFAULT_JWT_SECRET = "dev_jwt_secret_key_for_local_development_only_change_in_production"
if unified_config and hasattr(unified_config, "AUTH_JWT_SECRET"):
    AUTH_JWT_SECRET = unified_config.AUTH_JWT_SECRET
else:
    AUTH_JWT_SECRET = os.getenv("AUTH_JWT_SECRET", _DEFAULT_JWT_SECRET)
AUTH_JWT_ALGORITHM = "HS256"

security = HTTPBearer(auto_error=False)


@dataclass
class UserInfo:
    """用户信息"""
    user_id: int
    role: int
    username: str = ""

    @property
    def is_admin(self) -> bool:
        """是否为管理员"""
        return self.role <= 1


def decode_auth_token(token: str) -> Optional[dict]:
    """解码 auth 服务的 JWT Token"""
    if not AUTH_JWT_SECRET:
        logger.warning("AUTH_JWT_SECRET 未配置")
        return None

    try:
        payload = jwt.decode(token, AUTH_JWT_SECRET, algorithms=[AUTH_JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError as e:
        logger.debug(f"JWT 解码失败: {e}")
        return None


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> UserInfo:
    """获取当前用户（从 auth JWT 中）"""
    token = None

    # 从 Authorization header 获取
    if credentials:
        token = credentials.credentials

    # 从 X-Auth-Token header 获取
    if not token:
        token = request.headers.get("X-Auth-Token")

    if not token:
        raise HTTPException(status_code=401, detail="未登录")

    payload = decode_auth_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    user_id = int(payload.get("sub", 0))
    role = payload.get("role", 2)

    if not user_id:
        raise HTTPException(status_code=401, detail="Token 无效")

    return UserInfo(user_id=user_id, role=role)


async def require_user_admin(
    user: UserInfo = Depends(get_current_user)
) -> UserInfo:
    """要求管理员权限"""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
