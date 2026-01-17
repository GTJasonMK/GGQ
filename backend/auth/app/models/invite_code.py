"""
Invite Code Models
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.config import UserRole


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    created_by_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    role_grant: Mapped[int] = mapped_column(Integer, nullable=False, default=UserRole.USER)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    current_uses: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    @property
    def is_valid(self) -> bool:
        """检查邀请码是否有效"""
        if not self.is_active:
            return False
        if self.current_uses >= self.max_uses:
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True

    @property
    def remaining_uses(self) -> int:
        return max(0, self.max_uses - self.current_uses)


class InviteCodeUsage(Base):
    __tablename__ = "invite_code_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invite_code_id: Mapped[int] = mapped_column(Integer, ForeignKey("invite_codes.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    used_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
