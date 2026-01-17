"""
Invite Code Service
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invite_code import InviteCode, InviteCodeUsage
from app.models.user import User
from app.config import INVITE_CODE_LENGTH, DEFAULT_INVITE_CODE_EXPIRE_DAYS, UserRole


class InviteCodeService:
    """邀请码服务"""

    def _generate_code(self) -> str:
        """生成随机邀请码"""
        return secrets.token_urlsafe(INVITE_CODE_LENGTH)[:INVITE_CODE_LENGTH]

    async def get_by_id(self, db: AsyncSession, code_id: int) -> Optional[InviteCode]:
        """通过ID获取邀请码"""
        result = await db.execute(select(InviteCode).where(InviteCode.id == code_id))
        return result.scalar_one_or_none()

    async def get_by_code(self, db: AsyncSession, code: str) -> Optional[InviteCode]:
        """通过邀请码获取"""
        result = await db.execute(select(InviteCode).where(InviteCode.code == code))
        return result.scalar_one_or_none()

    async def create(
        self,
        db: AsyncSession,
        created_by_id: int,
        role_grant: int = UserRole.USER,
        max_uses: int = 1,
        expires_days: Optional[int] = DEFAULT_INVITE_CODE_EXPIRE_DAYS,
        note: Optional[str] = None
    ) -> InviteCode:
        """创建邀请码"""
        # 生成唯一邀请码
        code = self._generate_code()
        while await self.get_by_code(db, code):
            code = self._generate_code()

        expires_at = None
        if expires_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_days)

        invite_code = InviteCode(
            code=code,
            created_by_id=created_by_id,
            role_grant=role_grant,
            max_uses=max_uses,
            expires_at=expires_at,
            note=note
        )
        db.add(invite_code)
        await db.flush()
        await db.refresh(invite_code)
        return invite_code

    async def use(self, db: AsyncSession, invite_code: InviteCode, user_id: int) -> bool:
        """使用邀请码"""
        if not invite_code.is_valid:
            return False

        # 增加使用次数
        invite_code.current_uses += 1

        # 记录使用
        usage = InviteCodeUsage(
            invite_code_id=invite_code.id,
            user_id=user_id
        )
        db.add(usage)
        await db.flush()

        return True

    async def deactivate(self, db: AsyncSession, invite_code: InviteCode) -> InviteCode:
        """停用邀请码"""
        invite_code.is_active = False
        await db.flush()
        await db.refresh(invite_code)
        return invite_code

    async def delete(self, db: AsyncSession, invite_code: InviteCode) -> None:
        """删除邀请码"""
        # 先删除使用记录
        await db.execute(
            InviteCodeUsage.__table__.delete().where(
                InviteCodeUsage.invite_code_id == invite_code.id
            )
        )
        await db.delete(invite_code)
        await db.flush()

    async def get_list(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        created_by_id: Optional[int] = None,
        is_active: Optional[bool] = None
    ) -> tuple[list[InviteCode], int]:
        """获取邀请码列表"""
        query = select(InviteCode)

        if created_by_id is not None:
            query = query.where(InviteCode.created_by_id == created_by_id)
        if is_active is not None:
            query = query.where(InviteCode.is_active == is_active)

        # 计算总数
        count_query = select(func.count()).select_from(query.subquery())
        total = await db.scalar(count_query)

        # 分页
        query = query.order_by(InviteCode.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        codes = result.scalars().all()

        return list(codes), total or 0

    async def get_creator_username(self, db: AsyncSession, created_by_id: int) -> Optional[str]:
        """获取创建者用户名"""
        result = await db.execute(select(User.username).where(User.id == created_by_id))
        return result.scalar_one_or_none()


# 全局实例
invite_code_service = InviteCodeService()
