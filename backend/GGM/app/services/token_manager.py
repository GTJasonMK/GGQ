"""
API Token 管理服务（数据库版本）
- Token CRUD 操作
- 用量统计
- 数据库持久化
"""
import logging
import secrets
import time
from typing import List, Optional
from dataclasses import dataclass

from sqlalchemy import select, func

from app.database import async_session_factory
from app.db_models.api_token import ApiToken

logger = logging.getLogger(__name__)


@dataclass
class ApiTokenStats:
    """Token 统计信息"""
    total_tokens: int = 0
    enabled_tokens: int = 0
    total_requests: int = 0
    total_token_usage: int = 0


class TokenManager:
    """API Token 管理器（数据库版本）"""

    def __init__(self):
        self._legacy_tokens: set = set()  # 兼容旧配置中的静态 token

    def load(self, legacy_tokens: List[str] = None):
        """
        加载 Token 数据

        Args:
            legacy_tokens: 旧配置中的静态 token 列表（用于兼容）
        """
        # 加载旧配置中的静态 token
        if legacy_tokens:
            self._legacy_tokens = set(legacy_tokens)
            logger.info(f"加载 {len(self._legacy_tokens)} 个静态 Token（兼容模式）")

    async def create_token(self, name: str = "", expires_days: int = None, user_id: int = None) -> ApiToken:
        """
        创建新 Token

        Args:
            name: Token 名称/备注
            expires_days: 有效天数（None 表示永不过期）
            user_id: 关联用户ID

        Returns:
            新创建的 Token
        """
        token_str = secrets.token_urlsafe(32)

        async with async_session_factory() as session:
            # 获取当前 token 数量
            result = await session.execute(select(func.count(ApiToken.id)))
            count = result.scalar() or 0

            token = ApiToken(
                token=token_str,
                name=name or f"Token-{count + 1}",
                user_id=user_id,
                expires_at=time.time() + expires_days * 86400 if expires_days else None
            )
            session.add(token)
            await session.commit()
            await session.refresh(token)

            logger.info(f"创建 Token: {token.name}")
            return token

    async def get_token(self, token_str: str) -> Optional[ApiToken]:
        """获取 Token 对象"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(ApiToken).where(ApiToken.token == token_str)
            )
            return result.scalar_one_or_none()

    def verify_token(self, token_str: str) -> bool:
        """
        验证 Token 是否有效（同步版本，用于简单验证）

        Returns:
            True 如果 Token 有效
        """
        if not token_str:
            return False

        # 检查静态 Token（兼容旧配置）
        if token_str in self._legacy_tokens:
            return True

        # 异步验证需要在外部调用 verify_token_async
        return False

    async def verify_token_async(self, token_str: str) -> bool:
        """
        验证 Token 是否有效（异步版本）

        Returns:
            True 如果 Token 有效
        """
        if not token_str:
            return False

        # 检查静态 Token（兼容旧配置）
        if token_str in self._legacy_tokens:
            return True

        # 检查数据库中的 Token
        token = await self.get_token(token_str)
        if token and token.is_valid:
            return True

        return False

    async def record_usage(self, token_str: str, tokens: int = 0):
        """
        记录 Token 使用

        Args:
            token_str: Token 字符串
            tokens: 消耗的 token 数量（估算）
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(ApiToken).where(ApiToken.token == token_str)
            )
            token = result.scalar_one_or_none()

            if token:
                token.record_usage(tokens)
                await session.commit()

    async def list_tokens(self, include_legacy: bool = False) -> List[dict]:
        """
        列出所有 Token

        Args:
            include_legacy: 是否包含静态 Token

        Returns:
            Token 列表（隐藏完整 token）
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(ApiToken).order_by(ApiToken.created_at.desc())
            )
            tokens = result.scalars().all()

        result = [t.to_safe_dict() for t in tokens]

        if include_legacy:
            for legacy in self._legacy_tokens:
                result.append({
                    "token_prefix": legacy[:4] + "****" + legacy[-4:] if len(legacy) > 8 else "****",
                    "name": "(静态配置)",
                    "created_at": None,
                    "last_used_at": None,
                    "enabled": True,
                    "request_count": 0,
                    "token_usage": 0,
                    "is_legacy": True
                })

        return result

    async def delete_token(self, token_str: str) -> bool:
        """删除 Token"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(ApiToken).where(ApiToken.token == token_str)
            )
            token = result.scalar_one_or_none()

            if token:
                await session.delete(token)
                await session.commit()
                logger.info(f"删除 Token: {token_str[:8]}...")
                return True
        return False

    async def disable_token(self, token_str: str) -> bool:
        """禁用 Token"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(ApiToken).where(ApiToken.token == token_str)
            )
            token = result.scalar_one_or_none()

            if token:
                token.enabled = False
                await session.commit()
                logger.info(f"禁用 Token: {token.name}")
                return True
        return False

    async def enable_token(self, token_str: str) -> bool:
        """启用 Token"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(ApiToken).where(ApiToken.token == token_str)
            )
            token = result.scalar_one_or_none()

            if token:
                token.enabled = True
                await session.commit()
                logger.info(f"启用 Token: {token.name}")
                return True
        return False

    async def get_stats(self) -> ApiTokenStats:
        """获取统计信息"""
        async with async_session_factory() as session:
            # 总数
            total_result = await session.execute(select(func.count(ApiToken.id)))
            total = total_result.scalar() or 0

            # 启用数量
            enabled_result = await session.execute(
                select(func.count(ApiToken.id)).where(ApiToken.enabled == True)
            )
            enabled = enabled_result.scalar() or 0

            # 总请求数和 token 使用量
            stats_result = await session.execute(
                select(
                    func.sum(ApiToken.request_count),
                    func.sum(ApiToken.token_usage)
                )
            )
            stats = stats_result.one()
            total_requests = stats[0] or 0
            total_usage = stats[1] or 0

        return ApiTokenStats(
            total_tokens=total + len(self._legacy_tokens),
            enabled_tokens=enabled + len(self._legacy_tokens),
            total_requests=total_requests,
            total_token_usage=total_usage
        )

    async def get_token_by_prefix(self, prefix: str) -> Optional[ApiToken]:
        """通过前缀查找 Token（用于管理操作）"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(ApiToken).where(ApiToken.token.startswith(prefix))
            )
            return result.scalar_one_or_none()


# 全局 Token 管理器
token_manager = TokenManager()
