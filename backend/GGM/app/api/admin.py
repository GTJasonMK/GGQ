"""
管理员API路由
- 系统状态
- 账号管理（增删改查、测试）
- 配置管理（导入导出）
- 代理测试
"""
import logging
from typing import Optional, List

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.utils.auth import require_admin, create_admin_token, generate_api_token
from app.config import config_manager, AccountConfig
from app.services.account_manager import account_manager, CooldownReason
from app.services.conversation_manager import conversation_manager
from app.services.image_service import image_service
from app.services.jwt_service import jwt_service, get_http_client

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    password: str


class AccountCreate(BaseModel):
    team_id: str
    csesidx: str
    secure_c_ses: str
    host_c_oses: str = ""
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    note: str = ""
    available: bool = True


class AccountUpdate(BaseModel):
    team_id: Optional[str] = None
    csesidx: Optional[str] = None
    secure_c_ses: Optional[str] = None
    host_c_oses: Optional[str] = None
    user_agent: Optional[str] = None
    note: Optional[str] = None
    available: Optional[bool] = None


class ProxyTest(BaseModel):
    proxy: Optional[str] = None


class ConfigUpdate(BaseModel):
    proxy: Optional[str] = None
    admin_password: Optional[str] = None


@router.post("/login")
async def admin_login(request: LoginRequest):
    """
    管理员登录

    验证密码并返回管理员Token
    """
    config = config_manager.config
    if not config.admin_password_login_enabled:
        raise HTTPException(status_code=403, detail="已禁用管理员密码登录，请使用统一认证登录")

    # 验证密码
    if request.password != config.admin_password:
        raise HTTPException(status_code=401, detail="密码错误")

    # 生成Token
    token = create_admin_token(config.admin_secret_key, exp_seconds=86400)

    return {
        "token": token,
        "expires_in": 86400
    }


@router.get("/status")
async def get_system_status(token: str = Depends(require_admin)):
    """
    获取系统状态

    包括账号状态、会话统计等
    """
    account_status = account_manager.get_status()
    conversation_status = await conversation_manager.get_status()

    return {
        "accounts": account_status,
        "conversations": conversation_status,
        "config": {
            "proxy": config_manager.config.proxy or "未配置",
            "api_tokens_count": len(config_manager.config.api_tokens)
        }
    }


@router.get("/health")
async def get_health_summary(token: str = Depends(require_admin)):
    """
    获取账号池健康摘要

    返回所有账号的健康度评分、成功率、并发数等信息
    """
    return account_manager.get_health_summary()


# ==================== 账号管理 ====================

@router.get("/accounts")
async def list_accounts(token: str = Depends(require_admin)):
    """列出所有账号及其状态"""
    return account_manager.get_status()


@router.post("/accounts")
async def add_account(
    request: AccountCreate,
    token: str = Depends(require_admin)
):
    """添加新账号"""
    # 检查是否已存在相同csesidx的账号
    for acc in config_manager.config.accounts:
        if acc.csesidx == request.csesidx:
            raise HTTPException(status_code=400, detail="账号已存在（相同csesidx）")

    # 创建新账号配置
    new_account = AccountConfig(
        team_id=request.team_id,
        csesidx=request.csesidx,
        secure_c_ses=request.secure_c_ses,
        host_c_oses=request.host_c_oses,
        user_agent=request.user_agent,
        note=request.note,
        available=request.available
    )

    # 添加到配置
    config_manager.config.accounts.append(new_account)
    config_manager.save_config()

    # 重新加载账号
    account_manager.load_accounts()

    return {
        "success": True,
        "index": len(config_manager.config.accounts) - 1,
        "message": "账号添加成功"
    }


@router.put("/accounts/{index}")
async def update_account(
    index: int,
    request: AccountUpdate,
    token: str = Depends(require_admin)
):
    """更新账号配置"""
    if index < 0 or index >= len(config_manager.config.accounts):
        raise HTTPException(status_code=404, detail="账号不存在")

    acc_config = config_manager.config.accounts[index]
    account = account_manager.get_account(index)

    # 更新配置
    if request.team_id is not None:
        acc_config.team_id = request.team_id
    if request.csesidx is not None:
        acc_config.csesidx = request.csesidx
    if request.secure_c_ses is not None:
        acc_config.secure_c_ses = request.secure_c_ses
    if request.host_c_oses is not None:
        acc_config.host_c_oses = request.host_c_oses
    if request.user_agent is not None:
        acc_config.user_agent = request.user_agent
    if request.note is not None:
        acc_config.note = request.note
        if account:
            account.note = request.note
    if request.available is not None:
        acc_config.available = request.available
        if account:
            account.available = request.available

    config_manager.save_config()

    return {"success": True, "message": "账号更新成功"}


@router.delete("/accounts/{index}")
async def delete_account(
    index: int,
    token: str = Depends(require_admin)
):
    """删除账号"""
    if index < 0 or index >= len(config_manager.config.accounts):
        raise HTTPException(status_code=404, detail="账号不存在")

    # 从配置中删除
    config_manager.config.accounts.pop(index)
    config_manager.save_config()

    # 重新加载账号
    account_manager.load_accounts()

    return {"success": True, "message": "账号删除成功"}


@router.post("/accounts/{index}/toggle")
async def toggle_account(
    index: int,
    token: str = Depends(require_admin)
):
    """切换账号启用/禁用状态"""
    account = account_manager.get_account(index)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    # 切换状态
    new_state = not account.available
    account.available = new_state

    # 如果重新启用，清除冷却
    if new_state:
        account_manager.clear_account_cooldown(index)

    # 更新配置
    if index < len(config_manager.config.accounts):
        config_manager.config.accounts[index].available = new_state
        config_manager.save_config()

    return {
        "success": True,
        "available": new_state,
        "message": f"账号已{'启用' if new_state else '禁用'}"
    }


@router.get("/accounts/{index}/test")
async def test_account(
    index: int,
    token: str = Depends(require_admin)
):
    """测试账号JWT获取"""
    account = account_manager.get_account(index)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    try:
        # 强制刷新JWT
        jwt, expires_at = await jwt_service.get_jwt_for_account(account, force_refresh=True)
        return {
            "success": True,
            "message": "JWT获取成功",
            "expires_at": expires_at
        }
    except Exception as e:
        # 根据错误类型设置冷却
        error_msg = str(e)
        if "401" in error_msg or "认证" in error_msg:
            account_manager.mark_account_cooldown(index, CooldownReason.AUTH_ERROR)
        elif "429" in error_msg or "限额" in error_msg:
            account_manager.mark_account_cooldown(index, CooldownReason.RATE_LIMIT)
        else:
            account_manager.mark_account_cooldown(index, CooldownReason.GENERIC_ERROR)

        return {
            "success": False,
            "message": error_msg
        }


@router.post("/accounts/{index}/clear-cooldown")
async def clear_account_cooldown(
    index: int,
    token: str = Depends(require_admin)
):
    """清除账号冷却状态"""
    account = account_manager.get_account(index)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    account_manager.clear_account_cooldown(index)

    return {"message": f"账号 {index} 冷却已清除"}


@router.post("/accounts/{index}/cooldown")
async def set_account_cooldown(
    index: int,
    seconds: int = 300,
    reason: str = "manual",
    token: str = Depends(require_admin)
):
    """手动设置账号冷却"""
    account = account_manager.get_account(index)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    account_manager.mark_account_cooldown(
        index,
        CooldownReason.GENERIC_ERROR,
        custom_seconds=seconds
    )

    return {"message": f"账号 {index} 已进入冷却期 {seconds} 秒"}


# ==================== API Token管理 ====================

@router.get("/api-tokens")
async def list_api_tokens(token: str = Depends(require_admin)):
    """列出所有API Token（包含用量统计）"""
    from dataclasses import asdict
    from app.services.token_manager import token_manager

    tokens = await token_manager.list_tokens(include_legacy=True)
    stats = await token_manager.get_stats()

    return {
        "stats": asdict(stats),
        "tokens": tokens
    }


@router.post("/api-tokens")
async def create_api_token_route(
    name: str = "",
    expires_days: int = None,
    token: str = Depends(require_admin)
):
    """
    创建新的API Token

    Args:
        name: Token 名称/备注
        expires_days: 有效天数（不填则永不过期）
    """
    from app.services.token_manager import token_manager

    new_token = await token_manager.create_token(name=name, expires_days=expires_days)

    return {
        "token": new_token.token,  # 只有创建时返回完整 token
        "name": new_token.name,
        "expires_at": new_token.expires_at
    }


@router.get("/api-tokens/{token_prefix}")
async def get_api_token_detail(
    token_prefix: str,
    token: str = Depends(require_admin)
):
    """通过 Token 前缀获取详情"""
    from app.services.token_manager import token_manager

    api_token = await token_manager.get_token_by_prefix(token_prefix)
    if not api_token:
        raise HTTPException(status_code=404, detail="Token不存在")

    return api_token.to_dict(hide_token=True)


@router.delete("/api-tokens/{api_token}")
async def delete_api_token(
    api_token: str,
    token: str = Depends(require_admin)
):
    """删除API Token"""
    from app.services.token_manager import token_manager

    # 先尝试从新的 TokenManager 删除
    if await token_manager.delete_token(api_token):
        return {"deleted": True}

    # 再尝试从旧配置中删除
    if api_token in config_manager.config.api_tokens:
        config_manager.config.api_tokens.remove(api_token)
        config_manager.save_config()
        # 重新加载 token_manager
        token_manager.load(config_manager.config.api_tokens)
        return {"deleted": True}

    raise HTTPException(status_code=404, detail="Token不存在")


@router.post("/api-tokens/{token_prefix}/disable")
async def disable_api_token(
    token_prefix: str,
    token: str = Depends(require_admin)
):
    """禁用 Token"""
    from app.services.token_manager import token_manager

    api_token = await token_manager.get_token_by_prefix(token_prefix)
    if not api_token:
        raise HTTPException(status_code=404, detail="Token不存在")

    await token_manager.disable_token(api_token.token)
    return {"success": True, "message": "Token 已禁用"}


@router.post("/api-tokens/{token_prefix}/enable")
async def enable_api_token(
    token_prefix: str,
    token: str = Depends(require_admin)
):
    """启用 Token"""
    from app.services.token_manager import token_manager

    api_token = await token_manager.get_token_by_prefix(token_prefix)
    if not api_token:
        raise HTTPException(status_code=404, detail="Token不存在")

    await token_manager.enable_token(api_token.token)
    return {"success": True, "message": "Token 已启用"}


# ==================== 代理管理 ====================

@router.get("/proxy/status")
async def get_proxy_status(token: str = Depends(require_admin)):
    """获取代理状态"""
    proxy = config_manager.config.proxy
    if not proxy:
        return {"enabled": False, "url": None, "available": False}

    # 测试代理
    available = await _test_proxy(proxy)

    return {
        "enabled": True,
        "url": proxy,
        "available": available
    }


@router.post("/proxy/test")
async def test_proxy(
    request: ProxyTest,
    token: str = Depends(require_admin)
):
    """测试代理可用性"""
    proxy_url = request.proxy or config_manager.config.proxy

    if not proxy_url:
        return {"success": False, "message": "未配置代理地址"}

    available = await _test_proxy(proxy_url)

    return {
        "success": available,
        "message": "代理可用" if available else "代理不可用或连接超时"
    }


async def _test_proxy(proxy_url: str) -> bool:
    """测试代理是否可用"""
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url,
            verify=False,
            timeout=10.0
        ) as client:
            response = await client.get("https://www.google.com")
            return response.status_code == 200
    except Exception:
        return False


# ==================== 配置管理 ====================

@router.get("/config")
async def get_config(token: str = Depends(require_admin)):
    """获取当前配置（脱敏）"""
    config = config_manager.config

    return {
        "proxy": config.proxy or "未配置",
        "host": config.host,
        "port": config.port,
        "cooldown": config.cooldown.model_dump(),
        "models": [m.model_dump() for m in config.models],
        "accounts_count": len(config.accounts),
        "api_tokens_count": len(config.api_tokens)
    }


@router.put("/config")
async def update_config(
    request: ConfigUpdate,
    token: str = Depends(require_admin)
):
    """更新配置"""
    if request.proxy is not None:
        config_manager.config.proxy = request.proxy

    if request.admin_password is not None:
        config_manager.config.admin_password = request.admin_password

    config_manager.save_config()

    return {"success": True, "message": "配置已更新"}


@router.get("/config/export")
async def export_config(token: str = Depends(require_admin)):
    """导出完整配置"""
    return config_manager.config.model_dump()


@router.post("/config/import")
async def import_config(
    config_data: dict,
    token: str = Depends(require_admin)
):
    """导入配置"""
    try:
        from app.config import AppConfig

        # 验证配置格式
        new_config = AppConfig(**config_data)

        # 保存配置
        config_manager.config = new_config
        config_manager.save_config()

        # 重新加载账号
        account_manager.load_accounts()

        return {"success": True, "message": "配置导入成功"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"配置格式错误: {e}")


@router.post("/reload")
async def reload_config(token: str = Depends(require_admin)):
    """重新加载配置"""
    try:
        config_manager.load_config()
        account_manager.load_accounts()

        return {"message": "配置已重新加载"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重新加载失败: {e}")


# ==================== 系统清理 ====================

@router.post("/cleanup")
async def cleanup_system(
    max_conversation_age: int = 86400,
    max_image_age: int = 24,
    token: str = Depends(require_admin)
):
    """
    清理系统

    - 清理过期会话
    - 清理旧图片
    - 清理图片缓存
    """
    # 清理过期会话
    await conversation_manager.cleanup_expired(max_conversation_age)

    # 清理旧图片
    image_service.cleanup_old_images(max_image_age)

    # 清理图片缓存
    image_service.cleanup_cache()

    return {"message": "清理完成"}


# ==================== 凭证刷新服务 ====================

@router.get("/credentials/status")
async def get_credential_service_status(token: str = Depends(require_admin)):
    """获取凭证刷新服务状态"""
    from app.services.credential_service import credential_service
    return credential_service.get_status()


@router.post("/credentials/check/{index}")
async def check_account_credential(
    index: int,
    token: str = Depends(require_admin)
):
    """检查指定账号凭证是否有效"""
    from app.services.credential_service import credential_service

    account = account_manager.get_account(index)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    is_valid, error = await credential_service.check_credential(index)

    return {
        "account_index": index,
        "is_valid": is_valid,
        "error": error if not is_valid else None
    }


@router.post("/credentials/refresh/{index}")
async def refresh_account_credential(
    index: int,
    background: bool = True,
    token: str = Depends(require_admin)
):
    """
    刷新指定账号凭证

    Args:
        index: 账号索引
        background: 是否后台刷新（默认True，非阻塞）
    """
    from app.services.credential_service import credential_service

    account = account_manager.get_account(index)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    if background:
        # 后台刷新（非阻塞）
        await credential_service.queue_refresh(index)
        return {
            "account_index": index,
            "queued": True,
            "message": "已加入后台刷新队列"
        }
    else:
        # 同步刷新（阻塞）
        success, error = await credential_service.refresh_credential(index)
        return {
            "account_index": index,
            "success": success,
            "error": error if not success else None
        }


@router.post("/credentials/clear-invalid/{index}")
async def clear_invalid_status(
    index: int,
    token: str = Depends(require_admin)
):
    """清除账号的无效标记"""
    from app.services.credential_service import credential_service

    account = account_manager.get_account(index)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    # 从无效列表移除
    credential_service._invalid_accounts.discard(index)

    # 同时清除冷却状态
    account_manager.clear_account_cooldown(index)

    return {
        "account_index": index,
        "message": "已清除无效标记和冷却状态"
    }


# ==================== 账号同步 ====================

class AccountSyncRequest(BaseModel):
    """账号同步请求"""
    file_path: Optional[str] = None
    refresh_invalid: bool = True
    register_new: bool = True


@router.get("/accounts/sync/emails")
async def list_emails_from_file(
    file_path: Optional[str] = None,
    token: str = Depends(require_admin)
):
    """
    列出凭证文件中的所有邮箱

    返回邮箱列表以及是否已配置的状态
    """
    from app.services.credential_service import credential_service

    emails = credential_service._load_emails_from_file(file_path)

    results = []
    for email in emails:
        account_index, account_config = credential_service._find_account_by_email(email)
        results.append({
            "email": email,
            "configured": account_config is not None,
            "account_index": account_index,
            "note": account_config.note if account_config else None
        })

    return {
        "total": len(emails),
        "configured": sum(1 for r in results if r["configured"]),
        "unconfigured": sum(1 for r in results if not r["configured"]),
        "emails": results
    }


@router.post("/accounts/sync")
async def sync_accounts_from_file(
    request: AccountSyncRequest = AccountSyncRequest(),
    token: str = Depends(require_admin)
):
    """
    从凭证文件同步所有账号

    - 对于已配置但凭证无效的账号：刷新凭证
    - 对于未配置的新账号：执行注册/登录并添加到配置

    注意：此操作可能需要较长时间，每个账号需要约30-60秒
    """
    from app.services.credential_service import credential_service

    result = await credential_service.sync_accounts_from_file(
        file_path=request.file_path,
        refresh_invalid=request.refresh_invalid,
        register_new=request.register_new
    )

    return result


@router.post("/accounts/sync/single")
async def sync_single_account(
    email: str,
    token: str = Depends(require_admin)
):
    """
    同步单个账号

    如果账号已存在则刷新凭证，不存在则注册
    """
    from app.services.credential_service import credential_service

    result = await credential_service.sync_single_account(email)
    return result


@router.post("/accounts/register")
async def register_new_account(
    email: str,
    note: str = "",
    token: str = Depends(require_admin)
):
    """
    注册新账号

    使用指定邮箱进行注册/登录，并将凭证添加到配置
    """
    from app.services.credential_service import credential_service

    if not credential_service._auto_login_service:
        raise HTTPException(status_code=400, detail="自动登录服务未启用")

    # 检查账号是否已存在
    account_index, existing = credential_service._find_account_by_email(email)
    if existing:
        raise HTTPException(status_code=400, detail=f"账号已存在（索引: {account_index}）")

    # 执行注册
    credentials = await credential_service._auto_login_service.register_new_account(
        google_email=email,
        note=note if note else email.split("@")[0]
    )

    if credentials:
        credential_service._add_account_to_config(credentials)
        return {
            "success": True,
            "message": "注册成功",
            "credentials": {
                "team_id": credentials.get("team_id"),
                "note": credentials.get("note")
            }
        }
    else:
        raise HTTPException(status_code=500, detail="注册失败")


class ConcurrentSyncRequest(BaseModel):
    """并发同步请求"""
    file_path: Optional[str] = None
    max_concurrent: int = 5
    refresh_only: bool = False


@router.post("/accounts/sync/concurrent")
async def sync_accounts_concurrent(
    request: ConcurrentSyncRequest = ConcurrentSyncRequest(),
    token: str = Depends(require_admin)
):
    """
    并发同步账号（高速模式）

    使用多个浏览器实例同时刷新多个账号，大幅提升效率。
    验证码通过中心化轮询分发，避免冲突。

    Args:
        file_path: 凭证文件路径，默认 credient.txt
        max_concurrent: 最大并发数（默认 5，建议不超过 10）
        refresh_only: 仅刷新现有账号（不注册新账号）

    注意：
    - 需要配置 auto_login 和 qq_email
    - 并发数过高可能导致验证码匹配失败
    - 适合刷新大量已有账号
    """
    from app.services.credential_service import credential_service

    result = await credential_service.sync_accounts_concurrent(
        file_path=request.file_path,
        max_concurrent=request.max_concurrent,
        refresh_only=request.refresh_only
    )

    return result


# ==================== Token 申请审核 ====================

class RejectRequest(BaseModel):
    """拒绝申请请求"""
    reason: str = ""


@router.get("/token-requests")
async def list_token_requests(
    status: Optional[str] = None,
    token: str = Depends(require_admin)
):
    """获取 Token 申请列表"""
    from app.services.token_request_service import token_request_service

    all_requests = await token_request_service.get_all_requests()

    if status is not None:
        requests = [r for r in all_requests if r.status == status]
    else:
        requests = all_requests

    return {
        "total": len(requests),
        "requests": [r.to_dict() for r in requests]
    }


@router.get("/token-requests/pending")
async def list_pending_requests(token: str = Depends(require_admin)):
    """获取待审核的申请"""
    from app.services.token_request_service import token_request_service

    requests = await token_request_service.get_pending_requests()

    return {
        "total": len(requests),
        "requests": [r.to_dict() for r in requests]
    }


@router.post("/token-requests/{req_id}/approve")
async def approve_token_request(
    req_id: str,
    token: str = Depends(require_admin)
):
    """批准 Token 申请"""
    from app.services.token_request_service import token_request_service

    try:
        req = await token_request_service.approve_request(req_id, reviewer="admin")
        return {
            "success": True,
            "message": "申请已批准",
            "request": req.to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/token-requests/{req_id}/reject")
async def reject_token_request(
    req_id: str,
    request: RejectRequest,
    token: str = Depends(require_admin)
):
    """拒绝 Token 申请"""
    from app.services.token_request_service import token_request_service

    try:
        req = await token_request_service.reject_request(
            req_id,
            reviewer="admin",
            reason=request.reason
        )
        return {
            "success": True,
            "message": "申请已拒绝",
            "request": req.to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== 用户配额管理 ====================

class QuotaUpdate(BaseModel):
    """配额更新请求"""
    total_quota: Optional[int] = None
    unlimited: Optional[bool] = None
    reset_usage: bool = False


class QuotaAdd(BaseModel):
    """配额增加请求"""
    amount: int


@router.get("/quotas")
async def list_user_quotas(token: str = Depends(require_admin)):
    """获取所有用户配额列表"""
    from app.services.quota_service import quota_service

    quotas = await quota_service.list_quotas()
    stats = await quota_service.get_stats()

    return {
        "stats": stats,
        "quotas": [q.to_dict() for q in quotas]
    }


@router.get("/quotas/stats")
async def get_quota_stats(token: str = Depends(require_admin)):
    """获取配额统计信息"""
    from app.services.quota_service import quota_service

    return await quota_service.get_stats()


@router.get("/quotas/{user_id}")
async def get_user_quota(
    user_id: int,
    token: str = Depends(require_admin)
):
    """获取指定用户的配额"""
    from app.services.quota_service import quota_service

    quota = await quota_service.get_quota(user_id)
    return quota.to_dict()


@router.put("/quotas/{user_id}")
async def update_user_quota(
    user_id: int,
    request: QuotaUpdate,
    token: str = Depends(require_admin)
):
    """更新用户配额"""
    from app.services.quota_service import quota_service

    # 获取并更新配额
    if request.total_quota is not None:
        await quota_service.set_quota(user_id, request.total_quota)

    if request.unlimited is not None:
        await quota_service.set_unlimited(user_id, request.unlimited)

    if request.reset_usage:
        await quota_service.reset_usage(user_id)

    # 获取更新后的配额
    quota = await quota_service.get_quota(user_id)

    return {
        "success": True,
        "message": "配额已更新",
        "quota": quota.to_dict()
    }


@router.post("/quotas/{user_id}/add")
async def add_user_quota(
    user_id: int,
    request: QuotaAdd,
    token: str = Depends(require_admin)
):
    """增加用户配额"""
    from app.services.quota_service import quota_service

    quota = await quota_service.add_quota(user_id, request.amount)

    return {
        "success": True,
        "message": f"已增加 {request.amount} 配额",
        "quota": quota.to_dict()
    }


@router.post("/quotas/{user_id}/reset")
async def reset_user_quota(
    user_id: int,
    token: str = Depends(require_admin)
):
    """重置用户已使用配额"""
    from app.services.quota_service import quota_service

    quota = await quota_service.reset_usage(user_id)

    return {
        "success": True,
        "message": "已重置使用量",
        "quota": quota.to_dict()
    }


@router.post("/quotas/{user_id}/unlimited")
async def set_user_unlimited(
    user_id: int,
    unlimited: bool = True,
    token: str = Depends(require_admin)
):
    """设置用户是否无限制"""
    from app.services.quota_service import quota_service

    quota = await quota_service.set_unlimited(user_id, unlimited)

    return {
        "success": True,
        "message": f"已{'开启' if unlimited else '关闭'}无限制",
        "quota": quota.to_dict()
    }


# ==================== 统计分析 ====================

@router.get("/analytics/overview")
async def get_analytics_overview(token: str = Depends(require_admin)):
    """
    获取统计总览

    包括总用户数、总请求数、今日数据等
    """
    from app.services.analytics_service import analytics_service
    return await analytics_service.get_overview()


@router.get("/analytics/trend")
async def get_usage_trend(
    days: int = 7,
    token: str = Depends(require_admin)
):
    """
    获取使用趋势（按天统计）

    Args:
        days: 统计天数（默认7天）
    """
    from app.services.analytics_service import analytics_service
    return await analytics_service.get_usage_trend(days)


@router.get("/analytics/hourly")
async def get_hourly_distribution(
    days: int = 7,
    token: str = Depends(require_admin)
):
    """
    获取每小时请求分布

    Args:
        days: 统计天数（默认7天的平均值）
    """
    from app.services.analytics_service import analytics_service
    return await analytics_service.get_hourly_distribution(days)


@router.get("/analytics/models")
async def get_model_distribution(
    days: int = 30,
    token: str = Depends(require_admin)
):
    """
    获取模型使用分布

    Args:
        days: 统计天数（默认30天）
    """
    from app.services.analytics_service import analytics_service
    return await analytics_service.get_model_distribution(days)


@router.get("/analytics/sources")
async def get_source_distribution(
    days: int = 30,
    token: str = Depends(require_admin)
):
    """
    获取来源分布

    Args:
        days: 统计天数（默认30天）
    """
    from app.services.analytics_service import analytics_service
    return await analytics_service.get_source_distribution(days)


@router.get("/analytics/top-users")
async def get_top_users(
    limit: int = 10,
    days: int = 30,
    token: str = Depends(require_admin)
):
    """
    获取使用量最高的用户

    Args:
        limit: 返回数量（默认10）
        days: 统计天数（默认30天）
    """
    from app.services.analytics_service import analytics_service
    return await analytics_service.get_top_users(limit, days)


@router.get("/analytics/users/{user_id}")
async def get_user_analytics(
    user_id: int,
    days: int = 30,
    token: str = Depends(require_admin)
):
    """
    获取单个用户的详细统计

    Args:
        user_id: 用户ID
        days: 统计天数（默认30天）
    """
    from app.services.analytics_service import analytics_service
    return await analytics_service.get_user_detail(user_id, days)


@router.get("/analytics/errors")
async def get_error_stats(
    days: int = 7,
    token: str = Depends(require_admin)
):
    """
    获取错误统计

    Args:
        days: 统计天数（默认7天）
    """
    from app.services.analytics_service import analytics_service
    return await analytics_service.get_error_stats(days)


@router.get("/analytics/activity")
async def get_recent_activity(
    limit: int = 50,
    token: str = Depends(require_admin)
):
    """
    获取最近的活动记录

    Args:
        limit: 返回数量（默认50）
    """
    from app.services.analytics_service import analytics_service
    return await analytics_service.get_recent_activity(limit)


# ==================== 用户会话管理（管理员查看） ====================

@router.get("/users/{user_id}/conversations")
async def get_user_conversations(
    user_id: int,
    token: str = Depends(require_admin)
):
    """
    获取指定用户的所有会话列表

    Args:
        user_id: 用户ID
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.database import async_session_factory
    from app.db_models.conversation import Conversation

    async with async_session_factory() as session:
        result = await session.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        conversations = result.scalars().all()

        return {
            "user_id": user_id,
            "total": len(conversations),
            "conversations": [
                {
                    "id": conv.id,
                    "name": conv.name,
                    "model": conv.model,
                    "source": conv.source,
                    "username": conv.username,
                    "message_count": len(conv.messages) if conv.messages else 0,
                    "image_count": conv.image_count,
                    "created_at": conv.created_at,
                    "updated_at": conv.updated_at,
                    "preview": _get_conversation_preview(conv)
                }
                for conv in conversations
            ]
        }


@router.get("/conversations/{conversation_id}/detail")
async def get_conversation_detail_admin(
    conversation_id: str,
    token: str = Depends(require_admin)
):
    """
    获取会话详情（包含所有消息）

    Args:
        conversation_id: 会话ID
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.database import async_session_factory
    from app.db_models.conversation import Conversation

    async with async_session_factory() as session:
        result = await session.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one_or_none()

        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在")

        return {
            "id": conv.id,
            "name": conv.name,
            "model": conv.model,
            "system_prompt": conv.system_prompt,
            "user_id": conv.user_id,
            "username": conv.username,
            "source": conv.source,
            "account_index": conv.account_index,
            "image_count": conv.image_count,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
            "messages": [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "images": msg.images or []
                }
                for msg in (conv.messages or [])
            ]
        }


@router.get("/conversations/all")
async def list_all_conversations(
    limit: int = 100,
    offset: int = 0,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    source: Optional[str] = None,
    token: str = Depends(require_admin)
):
    """
    获取所有会话列表（管理员）

    Args:
        limit: 返回数量
        offset: 偏移量
        user_id: 按用户ID筛选
        username: 按用户名筛选（模糊匹配）
        source: 按来源筛选（web, cli, api）
    """
    from sqlalchemy import select, func
    from sqlalchemy.orm import selectinload
    from app.database import async_session_factory
    from app.db_models.conversation import Conversation

    async with async_session_factory() as session:
        # 构建查询
        query = select(Conversation).options(selectinload(Conversation.messages))

        if user_id is not None:
            query = query.where(Conversation.user_id == user_id)
        if username:
            # 支持模糊匹配
            query = query.where(Conversation.username.ilike(f"%{username}%"))
        if source:
            query = query.where(Conversation.source == source)

        # 获取总数
        count_query = select(func.count(Conversation.id))
        if user_id is not None:
            count_query = count_query.where(Conversation.user_id == user_id)
        if username:
            count_query = count_query.where(Conversation.username.ilike(f"%{username}%"))
        if source:
            count_query = count_query.where(Conversation.source == source)

        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        # 分页查询
        query = query.order_by(Conversation.updated_at.desc()).offset(offset).limit(limit)
        result = await session.execute(query)
        conversations = result.scalars().all()

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "conversations": [
                {
                    "id": conv.id,
                    "name": conv.name,
                    "model": conv.model,
                    "user_id": conv.user_id,
                    "username": conv.username,
                    "source": conv.source,
                    "message_count": len(conv.messages) if conv.messages else 0,
                    "image_count": conv.image_count,
                    "created_at": conv.created_at,
                    "updated_at": conv.updated_at,
                    "preview": _get_conversation_preview(conv)
                }
                for conv in conversations
            ]
        }


def _get_conversation_preview(conv) -> str:
    """获取会话预览（第一条用户消息的前100个字符）"""
    if not conv.messages:
        return ""
    for msg in conv.messages:
        if msg.role == "user" and msg.content:
            content = msg.content.strip()
            return content[:100] + "..." if len(content) > 100 else content
    return ""

