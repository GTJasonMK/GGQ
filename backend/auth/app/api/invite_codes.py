"""
Invite Code API Endpoints
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.auth import InviteCodeCreate, InviteCodeResponse, InviteCodeValidateResponse
from app.services.invite_code_service import invite_code_service
from app.api.deps import get_current_user, require_admin
from app.models.user import User
from app.config import UserRole

router = APIRouter(prefix="/api/invite-codes", tags=["invite-codes"])


@router.get("/validate/{code}", response_model=InviteCodeValidateResponse)
async def validate_invite_code(
    code: str,
    db: AsyncSession = Depends(get_db)
):
    """验证邀请码（公开接口）"""
    invite_code = await invite_code_service.get_by_code(db, code)

    if not invite_code:
        return InviteCodeValidateResponse(
            valid=False,
            message="邀请码不存在"
        )

    if not invite_code.is_valid:
        if not invite_code.is_active:
            message = "邀请码已停用"
        elif invite_code.current_uses >= invite_code.max_uses:
            message = "邀请码已用完"
        else:
            message = "邀请码已过期"

        return InviteCodeValidateResponse(
            valid=False,
            message=message
        )

    return InviteCodeValidateResponse(
        valid=True,
        role_grant=invite_code.role_grant,
        role_grant_name=UserRole.get_name(invite_code.role_grant),
        message="邀请码有效"
    )


@router.get("")
async def list_invite_codes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """获取邀请码列表（管理员+）"""
    # 非超管只能看自己创建的
    created_by_id = None if current_user.is_super_admin else current_user.id

    codes, total = await invite_code_service.get_list(
        db, page, page_size, created_by_id, is_active
    )

    result = []
    for code in codes:
        creator_username = await invite_code_service.get_creator_username(db, code.created_by_id)
        result.append(InviteCodeResponse(
            id=code.id,
            code=code.code,
            role_grant=code.role_grant,
            role_grant_name=UserRole.get_name(code.role_grant),
            max_uses=code.max_uses,
            current_uses=code.current_uses,
            remaining_uses=code.remaining_uses,
            expires_at=code.expires_at,
            created_at=code.created_at,
            is_active=code.is_active,
            is_valid=code.is_valid,
            note=code.note,
            created_by_username=creator_username
        ))

    return {
        "codes": result,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.post("", response_model=InviteCodeResponse)
async def create_invite_code(
    request: InviteCodeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """创建邀请码（管理员+）"""
    # 检查权限：只能创建授予比自己角色低的邀请码
    if not current_user.can_manage(request.role_grant):
        raise HTTPException(status_code=403, detail="无权创建授予该角色的邀请码")

    code = await invite_code_service.create(
        db,
        created_by_id=current_user.id,
        role_grant=request.role_grant,
        max_uses=request.max_uses,
        expires_days=request.expires_days,
        note=request.note
    )

    return InviteCodeResponse(
        id=code.id,
        code=code.code,
        role_grant=code.role_grant,
        role_grant_name=UserRole.get_name(code.role_grant),
        max_uses=code.max_uses,
        current_uses=code.current_uses,
        remaining_uses=code.remaining_uses,
        expires_at=code.expires_at,
        created_at=code.created_at,
        is_active=code.is_active,
        is_valid=code.is_valid,
        note=code.note,
        created_by_username=current_user.username
    )


@router.get("/{code_id}", response_model=InviteCodeResponse)
async def get_invite_code(
    code_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """获取邀请码详情（管理员+）"""
    code = await invite_code_service.get_by_id(db, code_id)
    if not code:
        raise HTTPException(status_code=404, detail="邀请码不存在")

    # 非超管只能查看自己创建的
    if not current_user.is_super_admin and code.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权查看该邀请码")

    creator_username = await invite_code_service.get_creator_username(db, code.created_by_id)

    return InviteCodeResponse(
        id=code.id,
        code=code.code,
        role_grant=code.role_grant,
        role_grant_name=UserRole.get_name(code.role_grant),
        max_uses=code.max_uses,
        current_uses=code.current_uses,
        remaining_uses=code.remaining_uses,
        expires_at=code.expires_at,
        created_at=code.created_at,
        is_active=code.is_active,
        is_valid=code.is_valid,
        note=code.note,
        created_by_username=creator_username
    )


@router.put("/{code_id}/deactivate", response_model=InviteCodeResponse)
async def deactivate_invite_code(
    code_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """停用邀请码（管理员+）"""
    code = await invite_code_service.get_by_id(db, code_id)
    if not code:
        raise HTTPException(status_code=404, detail="邀请码不存在")

    # 非超管只能停用自己创建的
    if not current_user.is_super_admin and code.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权停用该邀请码")

    code = await invite_code_service.deactivate(db, code)
    creator_username = await invite_code_service.get_creator_username(db, code.created_by_id)

    return InviteCodeResponse(
        id=code.id,
        code=code.code,
        role_grant=code.role_grant,
        role_grant_name=UserRole.get_name(code.role_grant),
        max_uses=code.max_uses,
        current_uses=code.current_uses,
        remaining_uses=code.remaining_uses,
        expires_at=code.expires_at,
        created_at=code.created_at,
        is_active=code.is_active,
        is_valid=code.is_valid,
        note=code.note,
        created_by_username=creator_username
    )


@router.delete("/{code_id}")
async def delete_invite_code(
    code_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """删除邀请码（管理员+）"""
    code = await invite_code_service.get_by_id(db, code_id)
    if not code:
        raise HTTPException(status_code=404, detail="邀请码不存在")

    # 非超管只能删除自己创建的
    if not current_user.is_super_admin and code.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权删除该邀请码")

    await invite_code_service.delete(db, code)

    return {"success": True, "message": "邀请码已删除"}
