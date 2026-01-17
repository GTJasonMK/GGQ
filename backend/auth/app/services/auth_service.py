"""
Auth Service
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.refresh_token import RefreshToken
from app.services.user_service import user_service
from app.services.invite_code_service import invite_code_service
from app.utils.password import verify_password
from app.utils.jwt_utils import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
    get_token_expire_seconds
)
from app.config import is_email_domain_allowed, UserRole


class AuthService:
    """认证服务"""

    async def register(
        self,
        db: AsyncSession,
        email: str,
        username: str,
        password: str,
        invite_code: Optional[str] = None
    ) -> tuple[Optional[User], Optional[str], Optional[str], Optional[str]]:
        """
        用户注册
        返回: (user, access_token, refresh_token, error_message)
        """
        # 检查邮箱是否在白名单域名中
        skip_invite_code = is_email_domain_allowed(email)

        # 确定用户角色
        user_role = UserRole.USER  # 默认普通用户

        if skip_invite_code:
            # 白名单邮箱，无需邀请码
            pass
        else:
            # 需要验证邀请码
            if not invite_code:
                return None, None, None, "需要邀请码才能注册"

            code_obj = await invite_code_service.get_by_code(db, invite_code)
            if not code_obj or not code_obj.is_valid:
                return None, None, None, "邀请码无效或已过期"

            user_role = code_obj.role_grant

        # 检查邮箱是否已存在
        if await user_service.email_exists(db, email):
            return None, None, None, "邮箱已被注册"

        # 检查用户名是否已存在
        if await user_service.username_exists(db, username):
            return None, None, None, "用户名已被使用"

        # 创建用户
        user = await user_service.create(
            db, email, username, password,
            role=user_role
        )

        # 如果使用了邀请码，标记已使用
        if not skip_invite_code and invite_code:
            code_obj = await invite_code_service.get_by_code(db, invite_code)
            if code_obj:
                await invite_code_service.use(db, code_obj, user.id)

        # 生成令牌
        access_token = create_access_token(user.id, user.role, user.username)
        refresh_token, token_hash, expires_at = create_refresh_token()

        # 保存刷新令牌
        rt = RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at
        )
        db.add(rt)

        return user, access_token, refresh_token, None

    async def login(
        self,
        db: AsyncSession,
        email_or_username: str,
        password: str,
        device_info: Optional[str] = None
    ) -> tuple[Optional[User], Optional[str], Optional[str], Optional[str]]:
        """
        用户登录
        返回: (user, access_token, refresh_token, error_message)
        """
        # 查找用户
        user = await user_service.get_by_email_or_username(db, email_or_username)
        if not user:
            return None, None, None, "用户不存在"

        # 验证密码
        if not verify_password(password, user.password_hash):
            return None, None, None, "密码错误"

        # 检查账号状态
        if not user.is_active:
            return None, None, None, "账号已被禁用"

        # 更新最后登录时间
        await user_service.update_last_login(db, user)

        # 生成令牌
        access_token = create_access_token(user.id, user.role, user.username)
        refresh_token, token_hash, expires_at = create_refresh_token()

        # 保存刷新令牌
        rt = RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            device_info=device_info
        )
        db.add(rt)

        return user, access_token, refresh_token, None

    async def refresh(
        self,
        db: AsyncSession,
        refresh_token: str
    ) -> tuple[Optional[User], Optional[str], Optional[str], Optional[str]]:
        """
        刷新令牌
        返回: (user, new_access_token, new_refresh_token, error_message)
        """
        # 查找刷新令牌
        token_hash = hash_refresh_token(refresh_token)
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        rt = result.scalar_one_or_none()

        if not rt or not rt.is_valid:
            return None, None, None, "刷新令牌无效或已过期"

        # 获取用户
        user = await user_service.get_by_id(db, rt.user_id)
        if not user or not user.is_active:
            return None, None, None, "用户不存在或已被禁用"

        # 撤销旧令牌
        rt.revoked = True
        rt.revoked_at = datetime.utcnow()

        # 生成新令牌
        new_access_token = create_access_token(user.id, user.role, user.username)
        new_refresh_token, new_token_hash, expires_at = create_refresh_token()

        # 保存新刷新令牌
        new_rt = RefreshToken(
            user_id=user.id,
            token_hash=new_token_hash,
            expires_at=expires_at,
            device_info=rt.device_info
        )
        db.add(new_rt)

        return user, new_access_token, new_refresh_token, None

    async def logout(self, db: AsyncSession, refresh_token: str) -> bool:
        """登出 - 撤销刷新令牌"""
        token_hash = hash_refresh_token(refresh_token)
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        rt = result.scalar_one_or_none()

        if rt and not rt.revoked:
            rt.revoked = True
            rt.revoked_at = datetime.utcnow()
            return True

        return False

    async def logout_all(self, db: AsyncSession, user_id: int) -> int:
        """登出所有设备 - 撤销用户的所有刷新令牌"""
        result = await db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked == False
            )
        )
        tokens = result.scalars().all()

        count = 0
        for rt in tokens:
            rt.revoked = True
            rt.revoked_at = datetime.utcnow()
            count += 1

        return count

    def get_token_expire_seconds(self) -> int:
        """获取访问令牌过期秒数"""
        return get_token_expire_seconds()


# 全局实例
auth_service = AuthService()
