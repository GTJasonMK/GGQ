"""
用户配额服务（数据库版本）
- 管理用户API调用配额
- 数据库持久化
"""
import logging
from typing import List, Optional

from sqlalchemy import select, func

from app.database import async_session_factory
from app.db_models.user_quota import UserQuota

logger = logging.getLogger(__name__)

# 默认配额（普通用户）
DEFAULT_QUOTA = 20


class QuotaService:
    """用户配额服务（数据库版本）"""

    def load(self):
        """兼容旧接口，数据库版本不需要加载"""
        pass

    async def get_quota(self, user_id: int) -> UserQuota:
        """
        获取用户配额（如果不存在则创建默认配额）

        Args:
            user_id: 用户ID

        Returns:
            用户配额对象
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(UserQuota).where(UserQuota.user_id == user_id)
            )
            quota = result.scalar_one_or_none()

            if not quota:
                # 创建默认配额
                quota = UserQuota(
                    user_id=user_id,
                    total_quota=DEFAULT_QUOTA
                )
                session.add(quota)
                await session.commit()
                await session.refresh(quota)

            return quota

    async def get_or_create_quota(self, user_id: int, username: str = "", is_admin: bool = False) -> UserQuota:
        """
        获取或创建用户配额

        Args:
            user_id: 用户ID
            username: 用户名
            is_admin: 是否为管理员（管理员无限制）

        Returns:
            用户配额对象
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(UserQuota).where(UserQuota.user_id == user_id)
            )
            quota = result.scalar_one_or_none()

            if not quota:
                # 创建新配额
                quota = UserQuota(
                    user_id=user_id,
                    username=username,
                    total_quota=DEFAULT_QUOTA,
                    unlimited=is_admin  # 管理员无限制
                )
                session.add(quota)
                await session.commit()
                await session.refresh(quota)
            else:
                # 更新用户名
                if username and quota.username != username:
                    quota.username = username
                    await session.commit()

            return quota

    async def check_and_consume(self, user_id: int, amount: int = 1) -> tuple:
        """
        检查配额并消耗

        Args:
            user_id: 用户ID
            amount: 消耗数量

        Returns:
            (是否成功, 剩余配额)
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(UserQuota).where(UserQuota.user_id == user_id)
            )
            quota = result.scalar_one_or_none()

            if not quota:
                quota = UserQuota(
                    user_id=user_id,
                    total_quota=DEFAULT_QUOTA
                )
                session.add(quota)
                await session.commit()
                await session.refresh(quota)

            if quota.is_exhausted:
                return False, quota.remaining

            success = quota.consume(amount)
            if success:
                await session.commit()

            return success, quota.remaining

    async def set_quota(self, user_id: int, total_quota: int, username: str = "") -> UserQuota:
        """
        设置用户配额

        Args:
            user_id: 用户ID
            total_quota: 新的总配额
            username: 用户名（可选）

        Returns:
            更新后的配额对象
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(UserQuota).where(UserQuota.user_id == user_id)
            )
            quota = result.scalar_one_or_none()

            if quota:
                quota.total_quota = total_quota
                if username:
                    quota.username = username
            else:
                quota = UserQuota(
                    user_id=user_id,
                    username=username,
                    total_quota=total_quota
                )
                session.add(quota)

            await session.commit()
            await session.refresh(quota)
            return quota

    async def add_quota(self, user_id: int, amount: int) -> UserQuota:
        """
        增加用户配额

        Args:
            user_id: 用户ID
            amount: 增加的配额数量

        Returns:
            更新后的配额对象
        """
        quota = await self.get_quota(user_id)

        async with async_session_factory() as session:
            result = await session.execute(
                select(UserQuota).where(UserQuota.user_id == user_id)
            )
            quota = result.scalar_one_or_none()

            if quota:
                quota.total_quota += amount
                await session.commit()
                await session.refresh(quota)

            return quota

    async def reset_usage(self, user_id: int) -> UserQuota:
        """
        重置用户已使用配额

        Args:
            user_id: 用户ID

        Returns:
            更新后的配额对象
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(UserQuota).where(UserQuota.user_id == user_id)
            )
            quota = result.scalar_one_or_none()

            if quota:
                quota.used_quota = 0
                await session.commit()
                await session.refresh(quota)

            return quota

    async def set_unlimited(self, user_id: int, unlimited: bool) -> UserQuota:
        """
        设置用户是否无限制

        Args:
            user_id: 用户ID
            unlimited: 是否无限制

        Returns:
            更新后的配额对象
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(UserQuota).where(UserQuota.user_id == user_id)
            )
            quota = result.scalar_one_or_none()

            if not quota:
                quota = UserQuota(
                    user_id=user_id,
                    total_quota=DEFAULT_QUOTA,
                    unlimited=unlimited
                )
                session.add(quota)
            else:
                quota.unlimited = unlimited

            await session.commit()
            await session.refresh(quota)
            return quota

    async def list_quotas(self) -> List[UserQuota]:
        """获取所有用户配额列表"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(UserQuota).order_by(UserQuota.used_quota.desc())
            )
            return list(result.scalars().all())

    async def get_stats(self) -> dict:
        """获取配额统计信息"""
        async with async_session_factory() as session:
            # 总用户数
            total_result = await session.execute(select(func.count(UserQuota.user_id)))
            total_users = total_result.scalar() or 0

            # 无限制用户数
            unlimited_result = await session.execute(
                select(func.count(UserQuota.user_id)).where(UserQuota.unlimited == True)
            )
            unlimited_users = unlimited_result.scalar() or 0

            # 配额耗尽用户数
            exhausted_result = await session.execute(
                select(func.count(UserQuota.user_id)).where(
                    (UserQuota.unlimited == False) &
                    (UserQuota.used_quota >= UserQuota.total_quota)
                )
            )
            exhausted_users = exhausted_result.scalar() or 0

            # 总使用量
            usage_result = await session.execute(select(func.sum(UserQuota.used_quota)))
            total_used = usage_result.scalar() or 0

            return {
                "total_users": total_users,
                "unlimited_users": unlimited_users,
                "exhausted_users": exhausted_users,
                "total_used": total_used,
                "default_quota": DEFAULT_QUOTA
            }


# 全局实例
quota_service = QuotaService()
