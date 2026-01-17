"""
User Service
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.config import UserRole
from app.utils.password import hash_password


class UserService:
    """用户服务"""

    async def get_by_id(self, db: AsyncSession, user_id: int) -> Optional[User]:
        """通过ID获取用户"""
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, db: AsyncSession, email: str) -> Optional[User]:
        """通过邮箱获取用户"""
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_username(self, db: AsyncSession, username: str) -> Optional[User]:
        """通过用户名获取用户"""
        result = await db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_by_email_or_username(self, db: AsyncSession, identifier: str) -> Optional[User]:
        """通过邮箱或用户名获取用户"""
        result = await db.execute(
            select(User).where(
                (User.email == identifier) | (User.username == identifier)
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        db: AsyncSession,
        email: str,
        username: str,
        password: str,
        role: int = UserRole.USER,
        created_by_id: Optional[int] = None
    ) -> User:
        """创建用户"""
        user = User(
            email=email,
            username=username,
            password_hash=hash_password(password),
            role=role,
            created_by_id=created_by_id
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

    async def update(
        self,
        db: AsyncSession,
        user: User,
        email: Optional[str] = None,
        username: Optional[str] = None,
        role: Optional[int] = None,
        is_active: Optional[bool] = None
    ) -> User:
        """更新用户"""
        if email is not None:
            user.email = email
        if username is not None:
            user.username = username
        if role is not None:
            user.role = role
        if is_active is not None:
            user.is_active = is_active

        user.updated_at = datetime.utcnow()
        await db.flush()
        await db.refresh(user)
        return user

    async def update_password(self, db: AsyncSession, user: User, new_password: str) -> User:
        """更新密码"""
        user.password_hash = hash_password(new_password)
        user.updated_at = datetime.utcnow()
        await db.flush()
        return user

    async def update_last_login(self, db: AsyncSession, user: User) -> User:
        """更新最后登录时间"""
        user.last_login_at = datetime.utcnow()
        await db.flush()
        return user

    async def delete(self, db: AsyncSession, user: User) -> None:
        """删除用户"""
        await db.delete(user)
        await db.flush()

    async def get_list(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        role: Optional[int] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None
    ) -> tuple[list[User], int]:
        """获取用户列表"""
        query = select(User)

        # 筛选条件
        if role is not None:
            query = query.where(User.role == role)
        if is_active is not None:
            query = query.where(User.is_active == is_active)
        if search:
            query = query.where(
                (User.email.contains(search)) |
                (User.username.contains(search))
            )

        # 计算总数
        count_query = select(func.count()).select_from(query.subquery())
        total = await db.scalar(count_query)

        # 分页
        query = query.order_by(User.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        users = result.scalars().all()

        return list(users), total or 0

    async def count(self, db: AsyncSession) -> int:
        """获取用户总数"""
        result = await db.scalar(select(func.count(User.id)))
        return result or 0

    async def email_exists(self, db: AsyncSession, email: str, exclude_id: Optional[int] = None) -> bool:
        """检查邮箱是否已存在"""
        query = select(User.id).where(User.email == email)
        if exclude_id:
            query = query.where(User.id != exclude_id)
        result = await db.execute(query)
        return result.scalar_one_or_none() is not None

    async def username_exists(self, db: AsyncSession, username: str, exclude_id: Optional[int] = None) -> bool:
        """检查用户名是否已存在"""
        query = select(User.id).where(User.username == username)
        if exclude_id:
            query = query.where(User.id != exclude_id)
        result = await db.execute(query)
        return result.scalar_one_or_none() is not None


# 全局实例
user_service = UserService()
