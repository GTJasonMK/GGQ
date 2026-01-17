"""
用户 Token 申请 API
- 提交申请
- 查看申请状态
- 获取已批准的 Token
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.utils.user_auth import get_current_user, UserInfo
from app.services.token_request_service import token_request_service

logger = logging.getLogger(__name__)

router = APIRouter()


class TokenRequestCreate(BaseModel):
    """创建申请请求"""
    reason: str = ""
    username: str = ""  # 用户名（前端传入）


class TokenRequestResponse(BaseModel):
    """申请响应"""
    id: str
    status: int
    status_text: str
    reason: str
    created_at: float
    reviewed_at: Optional[float] = None
    reject_reason: Optional[str] = None


@router.post("/token-requests")
async def create_token_request(
    request: TokenRequestCreate,
    user: UserInfo = Depends(get_current_user)
):
    """提交 Token 申请"""
    try:
        req = await token_request_service.create_request(
            user_id=user.user_id,
            username=request.username or f"user_{user.user_id}",
            reason=request.reason
        )
        return {
            "success": True,
            "message": "申请已提交，请等待管理员审核",
            "request": req.to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/token-requests/my")
async def get_my_requests(user: UserInfo = Depends(get_current_user)):
    """获取我的申请记录"""
    requests = await token_request_service.get_user_requests(user.user_id)
    return {
        "requests": [r.to_dict() for r in requests]
    }


@router.get("/token-requests/my-token")
async def get_my_token(user: UserInfo = Depends(get_current_user)):
    """获取我的 Token（如果已批准）"""
    token = await token_request_service.get_user_token(user.user_id)
    if token:
        return {
            "has_token": True,
            "token": token
        }
    return {
        "has_token": False,
        "token": None
    }
