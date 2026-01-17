"""
User Management API Endpoints
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.user import UserCreate, UserUpdate, UserResponse, UserListResponse
from app.services.user_service import user_service
from app.api.deps import get_current_user, require_admin
from app.models.user import User
from app.config import UserRole
from app.utils.password import check_password_strength

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=UserListResponse)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    role: Optional[int] = Query(None, ge=0, le=2),
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """获取用户列表（管理员+）"""
    users, total = await user_service.get_list(
        db, page, page_size, role, is_active, search
    )

    return UserListResponse(
        users=[
            UserResponse(
                id=u.id,
                email=u.email,
                username=u.username,
                role=u.role,
                role_name=u.role_name,
                is_active=u.is_active,
                created_at=u.created_at,
                last_login_at=u.last_login_at
            ) for u in users
        ],
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """获取用户详情（管理员+）"""
    user = await user_service.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    return UserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        role=user.role,
        role_name=user.role_name,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at
    )


@router.post("", response_model=UserResponse)
async def create_user(
    request: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """创建用户（管理员+）"""
    # 检查权限：只能创建比自己角色低的用户
    if not current_user.can_manage(request.role):
        raise HTTPException(status_code=403, detail="无权创建该角色的用户")

    # 检查邮箱是否已存在
    if await user_service.email_exists(db, request.email):
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    # 检查用户名是否已存在
    if await user_service.username_exists(db, request.username):
        raise HTTPException(status_code=400, detail="用户名已被使用")

    # 检查密码强度
    is_strong, msg = check_password_strength(request.password)
    if not is_strong:
        raise HTTPException(status_code=400, detail=msg)

    # 创建用户
    user = await user_service.create(
        db,
        email=request.email,
        username=request.username,
        password=request.password,
        role=request.role,
        created_by_id=current_user.id
    )

    return UserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        role=user.role,
        role_name=user.role_name,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at
    )


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    request: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """更新用户（管理员+）"""
    user = await user_service.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 检查权限：只能管理比自己角色低的用户
    if not current_user.can_manage(user.role):
        raise HTTPException(status_code=403, detail="无权修改该用户")

    # 如果要修改角色，检查是否有权限设置目标角色
    if request.role is not None and not current_user.can_manage(request.role):
        raise HTTPException(status_code=403, detail="无权设置该角色")

    # 检查邮箱冲突
    if request.email and await user_service.email_exists(db, request.email, exclude_id=user_id):
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    # 检查用户名冲突
    if request.username and await user_service.username_exists(db, request.username, exclude_id=user_id):
        raise HTTPException(status_code=400, detail="用户名已被使用")

    # 更新用户
    user = await user_service.update(
        db, user,
        email=request.email,
        username=request.username,
        role=request.role,
        is_active=request.is_active
    )

    return UserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        role=user.role,
        role_name=user.role_name,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at
    )


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """删除用户（管理员+）"""
    user = await user_service.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 不能删除自己
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己")

    # 检查权限
    if not current_user.can_manage(user.role):
        raise HTTPException(status_code=403, detail="无权删除该用户")

    await user_service.delete(db, user)

    return {"success": True, "message": "用户已删除"}


@router.put("/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """重置用户密码（管理员+）"""
    user = await user_service.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 检查权限
    if not current_user.can_manage(user.role):
        raise HTTPException(status_code=403, detail="无权重置该用户密码")

    # 生成随机密码
    import secrets
    new_password = secrets.token_urlsafe(12)

    await user_service.update_password(db, user, new_password)

    return {"success": True, "new_password": new_password, "message": "密码已重置"}
