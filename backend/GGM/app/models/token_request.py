"""
Token 申请数据模型
- 用户提交申请
- 管理员审核
"""
import time
from enum import IntEnum
from typing import Optional
from pydantic import BaseModel, Field


class RequestStatus(IntEnum):
    """申请状态"""
    PENDING = 0      # 待审核
    APPROVED = 1     # 已批准
    REJECTED = 2     # 已拒绝


class TokenRequest(BaseModel):
    """Token 申请记录"""
    id: str                                     # 申请ID
    user_id: int                                # 用户ID（来自auth服务）
    username: str                               # 用户名
    reason: str = ""                            # 申请理由
    status: RequestStatus = RequestStatus.PENDING
    token: Optional[str] = None                 # 批准后分配的Token
    created_at: float = Field(default_factory=time.time)
    reviewed_at: Optional[float] = None         # 审核时间
    reviewed_by: Optional[str] = None           # 审核人
    reject_reason: Optional[str] = None         # 拒绝理由

    def to_dict(self, hide_token: bool = True) -> dict:
        """转换为字典"""
        data = self.model_dump()
        data["status_text"] = self.status_text
        if hide_token and self.token and len(self.token) > 8:
            data["token"] = self.token[:4] + "****" + self.token[-4:]
        return data

    @property
    def status_text(self) -> str:
        """状态文本"""
        return {
            RequestStatus.PENDING: "待审核",
            RequestStatus.APPROVED: "已批准",
            RequestStatus.REJECTED: "已拒绝"
        }.get(self.status, "未知")
