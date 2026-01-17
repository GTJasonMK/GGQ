"""
User Quota Database Model
- 用户配额存储
"""
import time
from sqlalchemy import String, Integer, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserQuota(Base):
    """用户配额表"""
    __tablename__ = "user_quotas"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), default="")
    total_quota: Mapped[int] = mapped_column(Integer, default=20)
    used_quota: Mapped[int] = mapped_column(Integer, default=0)
    unlimited: Mapped[bool] = mapped_column(Boolean, default=False)

    # 时间
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)

    @property
    def remaining(self) -> int:
        """剩余配额"""
        if self.unlimited:
            return 999999
        return max(0, self.total_quota - self.used_quota)

    @property
    def is_exhausted(self) -> bool:
        """配额是否已用完"""
        if self.unlimited:
            return False
        return self.used_quota >= self.total_quota

    def consume(self, amount: int = 1) -> bool:
        """
        消耗配额

        Returns:
            是否成功消耗（配额不足返回False）
        """
        if self.unlimited:
            self.used_quota += amount
            self.updated_at = time.time()
            return True

        if self.used_quota + amount > self.total_quota:
            return False

        self.used_quota += amount
        self.updated_at = time.time()
        return True

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "total_quota": self.total_quota,
            "used_quota": self.used_quota,
            "remaining": self.remaining,
            "is_exhausted": self.is_exhausted,
            "unlimited": self.unlimited,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }
