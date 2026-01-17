"""
API Token Database Model
- API Token 存储和管理
"""
import time
from typing import Optional
from sqlalchemy import String, Integer, Float, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiToken(Base):
    """API Token 表"""
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # 关联用户（如果是用户申请的 Token）
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    # 使用统计
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)  # 总 token 消耗

    # 时间
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    last_used_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    expires_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    @property
    def is_expired(self) -> bool:
        """是否已过期"""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    @property
    def is_valid(self) -> bool:
        """是否有效（启用且未过期）"""
        return self.enabled and not self.is_expired

    def record_usage(self, tokens: int = 0):
        """记录使用"""
        self.request_count += 1
        self.token_usage += tokens
        self.last_used_at = time.time()

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "token": self.token,
            "name": self.name,
            "enabled": self.enabled,
            "user_id": self.user_id,
            "request_count": self.request_count,
            "token_usage": self.token_usage,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "expires_at": self.expires_at,
            "is_expired": self.is_expired,
            "is_valid": self.is_valid
        }

    def to_safe_dict(self) -> dict:
        """转换为安全字典（隐藏完整 token）"""
        return {
            "id": self.id,
            "token_prefix": self.token[:8] + "..." if self.token else "",
            "name": self.name,
            "enabled": self.enabled,
            "user_id": self.user_id,
            "request_count": self.request_count,
            "token_usage": self.token_usage,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "expires_at": self.expires_at,
            "is_expired": self.is_expired,
            "is_valid": self.is_valid
        }
