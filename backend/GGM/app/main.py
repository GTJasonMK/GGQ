"""
Gemini Business API 代理服务

主入口文件
- FastAPI 应用初始化
- 中间件配置
- 生命周期管理
"""
import logging
import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse

from app.config import config_manager, STATIC_DIR, IMAGES_DIR
from app.api import api_router
from app.database import init_db
from app.services.account_manager import account_manager
from app.services.conversation_manager import conversation_manager
from app.services.token_manager import token_manager
from app.services.token_request_service import token_request_service
from app.services.jwt_service import close_http_client
from app.services.image_service import image_service
from app.services.credential_service import credential_service
from app.services.account_replacement_service import account_replacement_service
from app.services.account_pool_service import account_pool_service
from app.services.quota_service import quota_service

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def precheck_credentials():
    """
    启动时预检查所有账号凭证

    检查每个账号的凭证是否有效，将无效账号标记并加入刷新队列
    这样可以避免首次请求时才发现凭证无效
    """
    accounts = account_manager.accounts
    if not accounts:
        return

    print(f"\n[启动] 正在检查 {len(accounts)} 个账号的凭证...")

    valid_count = 0
    invalid_count = 0

    # 并发检查所有账号（使用 quick_check_and_queue 限流）
    for account in accounts:
        if not account.available:
            continue

        try:
            is_valid = await credential_service.quick_check_and_queue(account.index)
            if is_valid:
                valid_count += 1
                print(f"  [OK] 账号 {account.index} ({account.note}) 凭证有效")
            else:
                invalid_count += 1
                print(f"  [!!] 账号 {account.index} ({account.note}) 凭证无效，已加入刷新队列")
        except Exception as e:
            invalid_count += 1
            print(f"  [!!] 账号 {account.index} ({account.note}) 检查出错: {e}")
            credential_service.mark_invalid(account.index)

    print(f"[启动] 凭证预检查完成: {valid_count} 个有效, {invalid_count} 个无效")
    logger.info(f"凭证预检查完成: {valid_count} 个有效, {invalid_count} 个无效")


async def sync_accounts_from_credient_file():
    """
    启动时从 credient.txt 同步账号

    读取 credient.txt 中的邮箱列表，对于：
    - 未在 config.json 中配置的邮箱：注册新账号
    - 已配置但凭证无效的邮箱：刷新凭证
    """
    from pathlib import Path

    # 检查是否启用了自动登录
    auto_login_config = config_manager.config.auto_login
    if not auto_login_config or not auto_login_config.enabled:
        print("[Startup] auto_login not enabled, skip account sync")
        return

    # 读取 credient.txt
    credient_file = Path(__file__).parent.parent / "credient.txt"
    if not credient_file.exists():
        print(f"[Startup] credient.txt not found, skip account sync")
        return

    try:
        with open(credient_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        emails = [line.strip() for line in lines if line.strip() and not line.startswith("#") and "@" in line]
    except Exception as e:
        print(f"[Startup] Failed to read credient.txt: {e}")
        return

    if not emails:
        print("[Startup] credient.txt is empty")
        return

    # 获取已配置的账号（通过 note 字段精确匹配邮箱前缀）
    configured_prefixes = set()
    for acc in config_manager.config.accounts:
        if acc.note:
            configured_prefixes.add(acc.note.lower())

    # 找出未配置的邮箱（精确匹配）
    new_emails = []
    for email in emails:
        email_prefix = email.split("@")[0].lower()
        # 检查是否已配置（精确匹配邮箱前缀）
        if email_prefix not in configured_prefixes:
            new_emails.append(email)

    if not new_emails:
        print(f"[Startup] All {len(emails)} emails in credient.txt are already configured")
        return

    print(f"\n[Startup] Found {len(new_emails)} unconfigured emails, starting registration:")
    for email in new_emails:
        print(f"  - {email}")

    # 调用 credential_service 同步
    print(f"\n[Startup] Starting concurrent account sync...")
    results = await credential_service.sync_accounts_from_file(
        refresh_invalid=False,  # 已有账号由 precheck_credentials 处理
        register_new=True,      # 注册新账号
        max_concurrent=5        # 并发注册，最多5个同时进行
    )

    # 显示结果
    print(f"\n[Startup] Account sync complete:")
    print(f"  - Registered: {results.get('new_accounts', 0)}")
    print(f"  - Refreshed: {results.get('refreshed_accounts', 0)}")
    print(f"  - Failed: {results.get('failed_accounts', 0)}")
    print(f"  - Skipped: {results.get('skipped_accounts', 0)}")

    # 如果有新账号，重新加载账号管理器
    if results.get('new_accounts', 0) > 0:
        account_manager.load_accounts()
        total, available = account_manager.get_account_count()
        print(f"[Startup] Accounts reloaded: {available}/{total} available")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    logger.info("正在启动 Gemini Business API 代理服务...")

    # 初始化数据库
    await init_db()
    logger.info("数据库已初始化")

    # 加载配置
    config_manager.load_config()
    logger.info(f"配置已加载: {len(config_manager.config.accounts)} 个账号")

    # 加载账号
    account_manager.load_accounts()
    total, available = account_manager.get_account_count()
    logger.info(f"账号状态: {available}/{total} 可用")

    # 加载 API Token（兼容旧配置中的静态 token）
    token_manager.load(config_manager.config.api_tokens)
    token_stats = await token_manager.get_stats()
    logger.info(f"API Token: {token_stats.enabled_tokens}/{token_stats.total_tokens} 可用")

    # Token 申请服务（数据库版本不需要加载）
    token_request_service.load()
    logger.info("Token 申请服务已就绪")

    # 用户配额服务（数据库版本不需要加载）
    quota_service.load()
    logger.info("用户配额服务已就绪")

    # 初始化凭证刷新服务
    await credential_service.initialize()
    if config_manager.config.auto_login and config_manager.config.auto_login.enabled:
        logger.info("凭证自动刷新服务已启用")

    # 启动定时清理任务
    cleanup_task = asyncio.create_task(periodic_cleanup())

    # 服务先启动，凭证检查和账号同步在后台进行
    logger.info("服务启动完成，凭证检查将在后台进行...")

    # 后台任务：账号同步和凭证检查
    async def background_credential_tasks():
        """后台执行凭证相关任务"""
        try:
            # 等待一小段时间，确保服务完全启动
            await asyncio.sleep(2)

            # 从 credient.txt 同步新账号
            await sync_accounts_from_credient_file()

            # 预检查所有账号凭证
            print("\n[后台] 正在预检查账号凭证...")
            await precheck_credentials()

            # 启动账号池维护服务（保持25个活跃账号）
            if config_manager.config.auto_login and config_manager.config.auto_login.enabled:
                await account_pool_service.start()
                logger.info("账号池维护服务已启动")
        except Exception as e:
            logger.error(f"后台凭证任务失败: {e}")

    # 启动后台凭证任务
    credential_task = asyncio.create_task(background_credential_tasks())

    yield

    # 关闭时
    logger.info("正在关闭服务...")

    # 取消后台任务
    credential_task.cancel()
    try:
        await credential_task
    except asyncio.CancelledError:
        pass

    # 取消清理任务
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    # 关闭凭证刷新服务
    await credential_service.shutdown()

    # 关闭并发刷新服务
    await credential_service.shutdown_concurrent_service()

    # 关闭账号替换服务
    await account_replacement_service.shutdown()

    # 关闭账号池维护服务
    await account_pool_service.stop()

    # 关闭HTTP客户端
    await close_http_client()

    logger.info("服务已关闭")


async def periodic_cleanup():
    """定时清理任务"""
    while True:
        try:
            await asyncio.sleep(3600)  # 每小时执行一次

            # 清理过期会话
            await conversation_manager.cleanup_expired(max_age_seconds=86400)

            # 清理图片缓存
            image_service.cleanup_cache()

            # 清理旧图片（保留24小时）
            image_service.cleanup_old_images(max_age_hours=24)

            # 衰减账号统计数据（每小时衰减10%，避免历史数据影响太大）
            account_manager.decay_statistics(decay_factor=0.9)

            logger.info("定时清理完成")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"定时清理失败: {e}")


# 创建FastAPI应用
app = FastAPI(
    title="Gemini Business API Proxy",
    description="Google Gemini Business API 代理服务，提供 OpenAI 兼容接口",
    version="1.0.0",
    lifespan=lifespan
)

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 请求日志中间件
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录请求日志"""
    logger.debug(f"{request.method} {request.url.path}")
    response = await call_next(request)

    # 注意：/v1/chat/completions 的 token 消耗在 chat.py 中记录
    # 这里只记录其他 /v1/ API 的调用次数（不含 token 消耗）
    if request.url.path.startswith("/v1/") and not request.url.path.endswith("/chat/completions"):
        if response.status_code == 200:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                api_token = auth_header[7:]
                # 只记录请求次数，不记录 token 消耗
                await token_manager.record_usage(api_token, 0)

    return response


# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理"""
    logger.exception(f"未处理的异常: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": {"message": str(exc), "type": "server_error"}}
    )


# 注册API路由
app.include_router(api_router)

# 挂载静态文件目录
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 挂载图片目录（用于直接访问图片）
if IMAGES_DIR.exists():
    app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")


# 聊天页面
@app.get("/chat", response_class=HTMLResponse)
async def chat_page():
    """聊天页面"""
    chat_file = STATIC_DIR / "chat.html"
    if chat_file.exists():
        return HTMLResponse(content=chat_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Chat page not found</h1>", status_code=404)


# 根路径
@app.get("/", response_class=HTMLResponse)
async def root():
    """根路径 - 返回管理界面或欢迎信息"""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))

    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gemini Business API Proxy</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
            h1 { color: #1a73e8; }
            .endpoint { background: #f5f5f5; padding: 10px; margin: 10px 0; border-radius: 4px; }
            code { background: #e8e8e8; padding: 2px 6px; border-radius: 3px; }
        </style>
    </head>
    <body>
        <h1>Gemini Business API Proxy</h1>
        <p>OpenAI 兼容的 Google Gemini Business API 代理服务</p>

        <h2>API 端点</h2>
        <div class="endpoint">
            <strong>POST</strong> <code>/v1/chat/completions</code> - 聊天补全
        </div>
        <div class="endpoint">
            <strong>GET</strong> <code>/v1/models</code> - 模型列表
        </div>
        <div class="endpoint">
            <strong>POST</strong> <code>/v1/files</code> - 文件上传
        </div>
        <div class="endpoint">
            <strong>GET</strong> <code>/api/admin/status</code> - 系统状态
        </div>

        <h2>文档</h2>
        <p>查看 <a href="/docs">API 文档</a> 获取详细信息</p>
    </body>
    </html>
    """)


# 健康检查
@app.get("/health")
async def health_check():
    """健康检查端点"""
    total, available = account_manager.get_account_count()
    return {
        "status": "healthy" if available > 0 else "degraded",
        "accounts": {"total": total, "available": available}
    }


def main():
    """主函数"""
    config = config_manager.config

    host = config.host if hasattr(config, 'host') else "0.0.0.0"
    port = config.port if hasattr(config, 'port') else 8000

    # 显示可访问的地址（0.0.0.0 不可直接访问，显示为 127.0.0.1）
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    logger.info(f"启动服务: http://{display_host}:{port}")
    logger.info(f"API 文档: http://{display_host}:{port}/docs")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="warning"  # 减少 uvicorn 日志，避免重复打印地址
    )


if __name__ == "__main__":
    main()
