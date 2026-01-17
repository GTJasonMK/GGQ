"""
Token Request Database Model
- Token 申请记录存储
"""
import time
from typing import Optional
from sqlalchemy import String, Integer, Float, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TokenRequest(Base):
    """Token 申请记录表"""
    __tablename__ = "token_requests"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text, default="")

    # 状态: pending, approved, rejected
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    # 审核信息
    reviewed_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 批准后的 Token
    token: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # 时间
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "reason": self.reason,
            "status": self.status,
            "reviewed_at": self.reviewed_at,
            "reviewed_by": self.reviewed_by,
            "reject_reason": self.reject_reason,
            "token": self.token,
            "created_at": self.created_at
        }

    def to_safe_dict(self) -> dict:
        """转换为安全字典（隐藏完整 token）"""
        result = self.to_dict()
        if result["token"]:
            result["token"] = result["token"][:8] + "..."
        return result
