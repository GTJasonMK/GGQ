"""
Auth Schemas
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field

from app.schemas.user import UserResponse


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_]+$')
    password: str = Field(..., min_length=8)
    invite_code: Optional[str] = Field(None, min_length=1)


class LoginRequest(BaseModel):
    email_or_username: str
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int  # 秒


class AuthResponse(BaseModel):
    user: UserResponse
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class InviteCodeCreate(BaseModel):
    role_grant: int = Field(default=2, ge=0, le=2)
    max_uses: int = Field(default=1, ge=1, le=1000)
    expires_days: Optional[int] = Field(default=7, ge=1, le=365)
    note: Optional[str] = Field(None, max_length=255)


class InviteCodeResponse(BaseModel):
    id: int
    code: str
    role_grant: int
    role_grant_name: str
    max_uses: int
    current_uses: int
    remaining_uses: int
    expires_at: Optional[datetime]
    created_at: datetime
    is_active: bool
    is_valid: bool
    note: Optional[str]
    created_by_username: Optional[str] = None

    class Config:
        from_attributes = True


class InviteCodeValidateResponse(BaseModel):
    valid: bool
    role_grant: Optional[int] = None
    role_grant_name: Optional[str] = None
    message: str


class SendVerificationCodeRequest(BaseModel):
    email: EmailStr


class SendVerificationCodeResponse(BaseModel):
    success: bool
    message: str


class VerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)


class VerifyCodeResponse(BaseModel):
    success: bool
    message: str


class RegisterWithVerificationRequest(BaseModel):
    """使用邮箱验证码注册（用于特定邮箱域名）"""
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_]+$')
    password: str = Field(..., min_length=8)
    verification_code: str = Field(..., min_length=6, max_length=6)
