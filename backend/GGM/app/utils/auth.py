"""
认证工具函数
- API Token 验证
- Admin Token 生成和验证
- 用户JWT验证
"""
import hmac
import hashlib
import base64
import json
import time
import secrets
from functools import wraps
from typing import Optional, Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials


security = HTTPBearer(auto_error=False)


@dataclass
class AuthResult:
    """认证结果"""
    token: str
    auth_type: str  # "api_token", "admin_token", "user_jwt"
    user_id: Optional[int] = None
    username: Optional[str] = None
    role: Optional[int] = None
    is_admin: bool = False


def create_admin_token(secret_key: str, exp_seconds: int = 86400) -> str:
    """
    创建管理员Token

    Args:
        secret_key: 密钥
        exp_seconds: 有效期（秒）

    Returns:
        token字符串
    """
    payload = {
        "exp": time.time() + exp_seconds,
        "ts": int(time.time())
    }
    payload_b = json.dumps(payload, separators=(",", ":")).encode()
    b64 = base64.urlsafe_b64encode(payload_b).decode().rstrip("=")
    signature = hmac.new(secret_key.encode(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{signature}"


def verify_admin_token(token: str, secret_key: str) -> bool:
    """
    验证管理员Token

    Args:
        token: Token字符串
        secret_key: 密钥

    Returns:
        是否有效
    """
    if not token:
        return False

    try:
        b64, sig = token.split(".", 1)
    except ValueError:
        return False

    # 验证签名
    expected_sig = hmac.new(secret_key.encode(), b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        return False

    # 验证过期时间
    padding = '=' * (-len(b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(b64 + padding).decode())
    except Exception:
        return False

    if payload.get("exp", 0) < time.time():
        return False

    return True


def generate_api_token() -> str:
    """生成新的API Token"""
    return secrets.token_urlsafe(32)


class AuthDependency:
    """认证依赖注入"""

    def __init__(self):
        self._config = None
        self._token_manager = None

    def set_config(self, config):
        """设置配置（延迟初始化）"""
        self._config = config

    @property
    def config(self):
        if self._config is None:
            from app.config import get_config
            self._config = get_config()
        return self._config

    @property
    def token_manager(self):
        if self._token_manager is None:
            from app.services.token_manager import token_manager
            self._token_manager = token_manager
        return self._token_manager

    async def require_api_auth(
        self,
        request: Request,
        credentials: HTTPAuthorizationCredentials = Depends(security)
    ) -> AuthResult:
        """
        验证API访问权限

        支持：
        - Authorization: Bearer <token>
        - X-API-Token: <token>
        - Cookie: admin_token=<token>

        认证规则：
        - 管理员Token/JWT：直接放行
        - 普通用户JWT + Web来源：允许访问（有配额限制）
        - 普通用户JWT + 非Web来源：拒绝，需使用API Token
        - API Token：允许访问（Token本身已经过审批）

        Returns:
            AuthResult 包含认证类型和用户信息
        """
        token = None

        # 1. 从 Authorization header 获取
        if credentials:
            token = credentials.credentials

        # 2. 从 X-API-Token header 获取
        if not token:
            token = request.headers.get("X-API-Token")

        # 3. 从 Cookie 获取
        if not token:
            token = request.cookies.get("admin_token")

        # 验证
        if not token:
            raise HTTPException(status_code=401, detail="未授权")

        # 先检查管理员 Token
        if verify_admin_token(token, self.config.admin_secret_key):
            return AuthResult(
                token=token,
                auth_type="admin_token",
                is_admin=True
            )

        # 检查用户JWT（来自auth服务）
        from app.utils.user_auth import decode_auth_token
        payload = decode_auth_token(token)
        if payload:
            user_id = int(payload.get("sub", 0))
            role = payload.get("role", 2)
            username = payload.get("username", "")
            is_admin = (role <= 1)

            # 管理员直接放行
            if is_admin:
                return AuthResult(
                    token=token,
                    auth_type="user_jwt",
                    user_id=user_id,
                    username=username,
                    role=role,
                    is_admin=True
                )

            # 获取客户端类型
            client_type = request.headers.get("X-Client-Type", "api")

            # Web来源的请求直接放行（有配额限制，在chat.py中处理）
            if client_type == "web":
                return AuthResult(
                    token=token,
                    auth_type="user_jwt",
                    user_id=user_id,
                    username=username,
                    role=role,
                    is_admin=False
                )

            # 非Web来源（外部API调用）不能使用JWT，需要使用API Token
            raise HTTPException(
                status_code=403,
                detail="外部API调用请使用API Token，请在Token申请页面申请"
            )

        # 检查 API Token（包含动态和静态 Token，都是审批后才有的）
        if await self.token_manager.verify_token_async(token):
            return AuthResult(
                token=token,
                auth_type="api_token"
            )

        raise HTTPException(status_code=401, detail="未授权")

    async def require_admin(
        self,
        request: Request,
        credentials: HTTPAuthorizationCredentials = Depends(security)
    ) -> str:
        """
        验证管理员权限

        支持：
        - Authorization: Bearer <token>（GGM admin token 或 auth服务JWT）
        - X-Admin-Token: <token>
        - Cookie: admin_token=<token>
        """
        token = None

        # 1. 从 Authorization header 获取
        if credentials:
            token = credentials.credentials

        # 2. 从 X-Admin-Token header 获取
        if not token:
            token = request.headers.get("X-Admin-Token")

        # 3. 从 Cookie 获取
        if not token:
            token = request.cookies.get("admin_token")

        if not token:
            raise HTTPException(status_code=401, detail="未授权")

        # 先验证GGM自己的admin token
        if verify_admin_token(token, self.config.admin_secret_key):
            return token

        # 再验证auth服务的JWT（管理员）
        from app.utils.user_auth import decode_auth_token
        payload = decode_auth_token(token)
        if payload:
            role = payload.get("role", 2)
            if role <= 1:  # SUPER_ADMIN(0) 或 ADMIN(1)
                return token

        raise HTTPException(status_code=401, detail="未授权")


# 全局认证依赖
auth = AuthDependency()
require_api_auth = auth.require_api_auth
require_admin = auth.require_admin
