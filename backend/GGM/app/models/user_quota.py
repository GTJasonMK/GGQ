"""
用户配额模型
- 管理用户的API调用配额
"""
from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class UserQuota:
    """用户配额"""
    user_id: int                      # 用户ID（来自auth服务）
    username: str = ""                # 用户名（用于显示）
    total_quota: int = 20             # 总配额（默认20次）
    used_quota: int = 0               # 已使用配额
    unlimited: bool = False           # 是否无限制（管理员）
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

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

    @classmethod
    def from_dict(cls, data: dict) -> "UserQuota":
        """从字典创建"""
        return cls(
            user_id=data["user_id"],
            username=data.get("username", ""),
            total_quota=data.get("total_quota", 20),
            used_quota=data.get("used_quota", 0),
            unlimited=data.get("unlimited", False),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time())
        )
