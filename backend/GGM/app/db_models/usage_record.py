"""
Usage Record Database Model
- 用户使用记录存储，用于统计分析
"""
import time
from typing import Optional
from sqlalchemy import String, Integer, Float, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UsageRecord(Base):
    """使用记录表"""
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 用户信息
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(100), default="")

    # 请求信息
    model: Mapped[str] = mapped_column(String(100), default="")
    source: Mapped[str] = mapped_column(String(20), default="web")  # web, cli, api
    conversation_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Token 信息
    api_token_prefix: Mapped[str] = mapped_column(String(20), default="")

    # 使用量
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # 状态
    success: Mapped[bool] = mapped_column(Integer, default=True)  # SQLite 用 Integer 表示 bool
    error_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # 时间
    timestamp: Mapped[float] = mapped_column(Float, default=time.time, index=True)

    # 复合索引用于时间范围查询
    __table_args__ = (
        Index('idx_usage_user_time', 'user_id', 'timestamp'),
        Index('idx_usage_date', 'timestamp'),
    )

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "model": self.model,
            "source": self.source,
            "conversation_id": self.conversation_id,
            "api_token_prefix": self.api_token_prefix,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "success": bool(self.success),
            "error_type": self.error_type,
            "timestamp": self.timestamp
        }
