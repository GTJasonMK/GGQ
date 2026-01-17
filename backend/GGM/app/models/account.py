"""
账号数据模型
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime


class CooldownReason(str, Enum):
    """冷却原因"""
    AUTH_ERROR = "auth_error"       # 认证错误
    RATE_LIMIT = "rate_limit"       # 触发限额
    GENERIC_ERROR = "generic_error" # 其他错误


class AccountState(BaseModel):
    """账号运行时状态"""
    jwt: Optional[str] = None
    jwt_expires_at: float = 0
    session_name: Optional[str] = None

    # 冷却状态
    cooldown_until: Optional[float] = None
    cooldown_reason: Optional[CooldownReason] = None

    # 统计信息
    total_requests: int = 0
    failed_requests: int = 0
    last_used_at: Optional[float] = None

    # 健康度相关字段
    concurrent_requests: int = 0          # 当前并发请求数
    consecutive_errors: int = 0           # 连续错误次数
    consecutive_successes: int = 0        # 连续成功次数
    last_success_at: Optional[float] = None  # 最后成功时间
    last_error_at: Optional[float] = None    # 最后错误时间
    total_response_time: float = 0        # 累计响应时间（毫秒）
    response_count: int = 0               # 响应计数（用于计算平均值）

    def is_jwt_valid(self, buffer_seconds: int = 30) -> bool:
        """检查JWT是否有效"""
        if not self.jwt:
            return False
        import time
        return time.time() < (self.jwt_expires_at - buffer_seconds)

    def is_in_cooldown(self) -> bool:
        """检查是否在冷却期"""
        if not self.cooldown_until:
            return False
        import time
        return time.time() < self.cooldown_until

    def get_cooldown_remaining(self) -> int:
        """获取剩余冷却时间（秒）"""
        if not self.cooldown_until:
            return 0
        import time
        remaining = int(self.cooldown_until - time.time())
        return max(0, remaining)

    def get_success_rate(self) -> float:
        """获取成功率 (0.0 - 1.0)"""
        if self.total_requests == 0:
            return 1.0  # 新账号默认100%成功率
        return (self.total_requests - self.failed_requests) / self.total_requests

    def get_avg_response_time(self) -> float:
        """获取平均响应时间（毫秒）"""
        if self.response_count == 0:
            return 0
        return self.total_response_time / self.response_count

    def record_request_start(self):
        """记录请求开始"""
        import time
        self.concurrent_requests += 1
        self.last_used_at = time.time()

    def record_request_end(self, success: bool, response_time_ms: float = 0):
        """记录请求结束"""
        import time
        self.concurrent_requests = max(0, self.concurrent_requests - 1)
        self.total_requests += 1

        if success:
            self.consecutive_successes += 1
            self.consecutive_errors = 0
            self.last_success_at = time.time()
        else:
            self.consecutive_errors += 1
            self.consecutive_successes = 0
            self.failed_requests += 1
            self.last_error_at = time.time()

        # 记录响应时间
        if response_time_ms > 0:
            self.total_response_time += response_time_ms
            self.response_count += 1


class Account(BaseModel):
    """账号完整信息（配置+状态）"""
    index: int
    team_id: str
    csesidx: str
    secure_c_ses: str
    host_c_oses: str = ""
    user_agent: str = ""
    note: str = ""
    refresh_time: str = ""  # 凭据刷新时间 (ISO 格式)

    # 配置状态
    available: bool = True

    # 运行时状态
    state: AccountState = Field(default_factory=AccountState)

    def is_usable(self) -> bool:
        """检查账号是否可用"""
        return self.available and not self.state.is_in_cooldown()

    def get_refresh_datetime(self) -> Optional[datetime]:
        """获取刷新时间的 datetime 对象"""
        if not self.refresh_time:
            return None
        try:
            return datetime.fromisoformat(self.refresh_time.replace('Z', '+00:00'))
        except:
            return None

    def to_display_dict(self) -> dict:
        """转换为显示用的字典（隐藏敏感信息）"""
        return {
            "index": self.index,
            "team_id": self.team_id[:20] + "..." if len(self.team_id) > 20 else self.team_id,
            "csesidx": self.csesidx,
            "available": self.available,
            "is_usable": self.is_usable(),
            "has_jwt": self.state.jwt is not None,
            "has_session": self.state.session_name is not None,
            "cooldown_remaining": self.state.get_cooldown_remaining(),
            "cooldown_reason": self.state.cooldown_reason.value if self.state.cooldown_reason else None,
            "total_requests": self.state.total_requests,
            "failed_requests": self.state.failed_requests,
            "note": self.note,
            "refresh_time": self.refresh_time,
            # 健康度相关
            "concurrent_requests": self.state.concurrent_requests,
            "consecutive_errors": self.state.consecutive_errors,
            "consecutive_successes": self.state.consecutive_successes,
            "success_rate": round(self.state.get_success_rate() * 100, 1),
            "avg_response_time_ms": round(self.state.get_avg_response_time(), 1)
        }
