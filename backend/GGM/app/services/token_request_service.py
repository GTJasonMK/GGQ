"""
Token 申请服务（数据库版本）
- 申请 CRUD 操作
- 审核流程
- 数据库持久化
"""
import logging
import secrets
import time
from typing import List, Optional

from sqlalchemy import select

from app.database import async_session_factory
from app.db_models.token_request import TokenRequest
from app.services.token_manager import token_manager
from app.services.quota_service import quota_service

logger = logging.getLogger(__name__)


class TokenRequestService:
    """Token 申请服务（数据库版本）"""

    def load(self):
        """兼容旧接口，数据库版本不需要加载"""
        pass

    async def create_request(self, user_id: int, username: str, reason: str = "") -> TokenRequest:
        """创建申请"""
        async with async_session_factory() as session:
            # 检查是否已有待审核的申请
            result = await session.execute(
                select(TokenRequest).where(
                    (TokenRequest.user_id == user_id) &
                    (TokenRequest.status == "pending")
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                raise ValueError("您已有待审核的申请")

            req_id = f"req_{int(time.time())}_{secrets.token_hex(4)}"
            request = TokenRequest(
                id=req_id,
                user_id=user_id,
                username=username,
                reason=reason,
                status="pending"
            )
            session.add(request)
            await session.commit()
            await session.refresh(request)

            logger.info(f"用户 {username} 提交了Token申请")
            return request

    async def get_user_requests(self, user_id: int) -> List[TokenRequest]:
        """获取用户的申请记录"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(TokenRequest)
                .where(TokenRequest.user_id == user_id)
                .order_by(TokenRequest.created_at.desc())
            )
            return list(result.scalars().all())

    async def get_user_token(self, user_id: int) -> Optional[str]:
        """获取用户的Token（如果已批准）"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(TokenRequest).where(
                    (TokenRequest.user_id == user_id) &
                    (TokenRequest.status == "approved")
                )
            )
            request = result.scalar_one_or_none()
            return request.token if request else None

    async def get_pending_requests(self) -> List[TokenRequest]:
        """获取待审核的申请"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(TokenRequest)
                .where(TokenRequest.status == "pending")
                .order_by(TokenRequest.created_at.asc())
            )
            return list(result.scalars().all())

    async def get_all_requests(self) -> List[TokenRequest]:
        """获取所有申请"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(TokenRequest).order_by(TokenRequest.created_at.desc())
            )
            return list(result.scalars().all())

    async def approve_request(self, req_id: str, reviewer: str) -> TokenRequest:
        """批准申请"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(TokenRequest).where(TokenRequest.id == req_id)
            )
            request = result.scalar_one_or_none()

            if not request:
                raise ValueError("申请不存在")
            if request.status != "pending":
                raise ValueError("该申请已处理")

            # 创建Token
            token = await token_manager.create_token(
                name=f"User-{request.username}",
                expires_days=None,  # 永不过期
                user_id=request.user_id
            )

            request.status = "approved"
            request.token = token.token
            request.reviewed_at = time.time()
            request.reviewed_by = reviewer

            await session.commit()

            # 解除用户配额限制
            await quota_service.set_unlimited(request.user_id, True)
            logger.info(f"用户 {request.username} 的配额限制已解除")

            logger.info(f"申请 {req_id} 已批准，用户: {request.username}")
            return request

    async def reject_request(self, req_id: str, reviewer: str, reason: str = "") -> TokenRequest:
        """拒绝申请"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(TokenRequest).where(TokenRequest.id == req_id)
            )
            request = result.scalar_one_or_none()

            if not request:
                raise ValueError("申请不存在")
            if request.status != "pending":
                raise ValueError("该申请已处理")

            request.status = "rejected"
            request.reviewed_at = time.time()
            request.reviewed_by = reviewer
            request.reject_reason = reason

            await session.commit()

            logger.info(f"申请 {req_id} 已拒绝，用户: {request.username}")
            return request

    async def get_request(self, req_id: str) -> Optional[TokenRequest]:
        """获取申请详情"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(TokenRequest).where(TokenRequest.id == req_id)
            )
            return result.scalar_one_or_none()


# 全局服务实例
token_request_service = TokenRequestService()
