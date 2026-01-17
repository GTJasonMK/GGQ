"""
GGM Database Configuration
- SQLite + async SQLAlchemy
- Database session management
"""
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import DATA_DIR

# 数据库文件路径
DATABASE_PATH = DATA_DIR / "ggm.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH}"

# 确保数据目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 创建异步引擎
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True
)

# 创建异步会话工厂
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


async def get_db():
    """获取数据库会话的依赖"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """初始化数据库表"""
    # 导入所有模型以确保它们被注册
    from app.db_models import conversation, api_token, token_request, user_quota

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """关闭数据库连接"""
    await engine.dispose()
