"""
Auth Service - FastAPI Application
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS
from app.database import init_db, async_session_factory
from app.api.auth import router as auth_router
from app.api.users import router as users_router
from app.api.invite_codes import router as invite_codes_router

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def create_initial_admin():
    """创建初始超级管理员"""
    from app.services.user_service import user_service
    from app.config import (
        INITIAL_ADMIN_EMAIL,
        INITIAL_ADMIN_USERNAME,
        INITIAL_ADMIN_PASSWORD,
        UserRole
    )

    async with async_session_factory() as db:
        try:
            # 检查是否已有用户
            count = await user_service.count(db)
            if count > 0:
                logger.info(f"数据库已有 {count} 个用户，跳过初始化")
                return

            # 创建超级管理员
            admin = await user_service.create(
                db,
                email=INITIAL_ADMIN_EMAIL,
                username=INITIAL_ADMIN_USERNAME,
                password=INITIAL_ADMIN_PASSWORD,
                role=UserRole.SUPER_ADMIN
            )
            await db.commit()

            logger.info(f"超级管理员已创建: {admin.username} ({admin.email})")
            print(f"\n{'='*50}")
            print("初始超级管理员已创建:")
            print(f"  用户名: {INITIAL_ADMIN_USERNAME}")
            print(f"  邮箱: {INITIAL_ADMIN_EMAIL}")
            print(f"  密码: {INITIAL_ADMIN_PASSWORD}")
            print("请首次登录后修改密码!")
            print(f"{'='*50}\n")

        except Exception as e:
            logger.error(f"创建初始管理员失败: {e}")
            await db.rollback()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    logger.info("初始化数据库...")
    await init_db()

    logger.info("检查初始管理员...")
    await create_initial_admin()

    logger.info("Auth 服务启动完成")
    yield

    # 关闭时
    logger.info("Auth 服务关闭")


# 创建应用
app = FastAPI(
    title="Auth Service",
    description="统一认证服务 - 用户管理、角色权限、邀请码注册",
    version="1.0.0",
    lifespan=lifespan
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS + ["*"],  # 开发阶段允许所有来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(invite_codes_router)


@app.get("/")
async def root():
    """健康检查"""
    return {
        "service": "Auth Service",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health():
    """健康检查端点"""
    return {"status": "healthy"}
