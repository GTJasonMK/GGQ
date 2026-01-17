"""
Auth API Endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.auth import (
    RegisterRequest, LoginRequest, PasswordChangeRequest,
    AuthResponse, RefreshRequest, TokenResponse,
    SendVerificationCodeRequest, SendVerificationCodeResponse,
    VerifyCodeRequest, VerifyCodeResponse,
    RegisterWithVerificationRequest
)
from app.schemas.user import UserResponse
from app.services.auth_service import auth_service
from app.services.user_service import user_service
from app.api.deps import get_current_user
from app.models.user import User
from app.utils.password import verify_password, check_password_strength
from app.services.verification_service import verification_service
from app.services.email_service import email_service
from app.config import is_email_domain_allowed, EMAIL_VERIFICATION_ENABLED

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse)
async def register(
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    """用户注册（需要邀请码）"""
    # 检查密码强度
    is_strong, msg = check_password_strength(request.password)
    if not is_strong:
        raise HTTPException(status_code=400, detail=msg)

    user, access_token, refresh_token, error = await auth_service.register(
        db,
        email=request.email,
        username=request.username,
        password=request.password,
        invite_code=request.invite_code
    )

    if error:
        raise HTTPException(status_code=400, detail=error)

    return AuthResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            username=user.username,
            role=user.role,
            role_name=user.role_name,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at
        ),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=auth_service.get_token_expire_seconds()
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    req: Request,
    db: AsyncSession = Depends(get_db)
):
    """用户登录"""
    device_info = req.headers.get("User-Agent", "")[:500]

    user, access_token, refresh_token, error = await auth_service.login(
        db,
        email_or_username=request.email_or_username,
        password=request.password,
        device_info=device_info
    )

    if error:
        raise HTTPException(status_code=401, detail=error)

    return AuthResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            username=user.username,
            role=user.role,
            role_name=user.role_name,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at
        ),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=auth_service.get_token_expire_seconds()
    )


@router.post("/logout")
async def logout(
    request: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """用户登出"""
    success = await auth_service.logout(db, request.refresh_token)
    return {"success": success, "message": "已登出" if success else "令牌无效"}


@router.post("/logout-all")
async def logout_all(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """登出所有设备"""
    count = await auth_service.logout_all(db, current_user.id)
    return {"success": True, "message": f"已撤销 {count} 个会话"}


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshRequest,
    db: AsyncSession = Depends(get_db)
):
    """刷新访问令牌"""
    user, access_token, refresh_token, error = await auth_service.refresh(
        db, request.refresh_token
    )

    if error:
        raise HTTPException(status_code=401, detail=error)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=auth_service.get_token_expire_seconds()
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        role=current_user.role,
        role_name=current_user.role_name,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
        last_login_at=current_user.last_login_at
    )


@router.put("/password")
async def change_password(
    request: PasswordChangeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """修改密码"""
    # 验证当前密码
    if not verify_password(request.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码错误")

    # 检查新密码强度
    is_strong, msg = check_password_strength(request.new_password)
    if not is_strong:
        raise HTTPException(status_code=400, detail=msg)

    # 更新密码
    await user_service.update_password(db, current_user, request.new_password)

    # 撤销所有刷新令牌（强制重新登录）
    await auth_service.logout_all(db, current_user.id)

    return {"success": True, "message": "密码已修改，请重新登录"}


@router.post("/verification/send", response_model=SendVerificationCodeResponse)
async def send_verification_code(request: SendVerificationCodeRequest):
    """发送邮箱验证码（仅限允许的邮箱域名）"""
    # 检查邮件验证功能是否启用
    if not EMAIL_VERIFICATION_ENABLED:
        raise HTTPException(status_code=400, detail="邮箱验证功能未启用")

    # 检查邮件服务是否配置
    if not email_service.is_configured():
        raise HTTPException(status_code=500, detail="邮件服务未配置")

    # 检查邮箱域名是否在允许列表中
    if not is_email_domain_allowed(request.email):
        raise HTTPException(status_code=400, detail="该邮箱域名不支持验证码注册")

    # 发送验证码
    success, message = verification_service.send_code(request.email)

    return SendVerificationCodeResponse(success=success, message=message)


@router.post("/verification/verify", response_model=VerifyCodeResponse)
async def verify_code(request: VerifyCodeRequest):
    """验证邮箱验证码"""
    success, message = verification_service.verify_code(request.email, request.code)
    return VerifyCodeResponse(success=success, message=message)


@router.post("/register-with-verification", response_model=AuthResponse)
async def register_with_verification(
    request: RegisterWithVerificationRequest,
    db: AsyncSession = Depends(get_db)
):
    """使用邮箱验证码注册（仅限允许的邮箱域名）"""
    # 检查邮件验证功能是否启用
    if not EMAIL_VERIFICATION_ENABLED:
        raise HTTPException(status_code=400, detail="邮箱验证功能未启用")

    # 检查邮箱域名是否在允许列表中
    if not is_email_domain_allowed(request.email):
        raise HTTPException(status_code=400, detail="该邮箱域名不支持验证码注册")

    # 验证验证码
    code_valid, code_message = verification_service.verify_code(
        request.email, request.verification_code
    )
    if not code_valid:
        raise HTTPException(status_code=400, detail=code_message)

    # 检查密码强度
    is_strong, msg = check_password_strength(request.password)
    if not is_strong:
        raise HTTPException(status_code=400, detail=msg)

    # 注册用户（不需要邀请码）
    user, access_token, refresh_token, error = await auth_service.register(
        db,
        email=request.email,
        username=request.username,
        password=request.password,
        invite_code=None  # 验证码注册不需要邀请码
    )

    if error:
        raise HTTPException(status_code=400, detail=error)

    return AuthResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            username=user.username,
            role=user.role,
            role_name=user.role_name,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at
        ),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=auth_service.get_token_expire_seconds()
    )


@router.get("/verification/status")
async def get_verification_status():
    """获取邮箱验证功能状态"""
    return {
        "enabled": EMAIL_VERIFICATION_ENABLED,
        "email_configured": email_service.is_configured()
    }
