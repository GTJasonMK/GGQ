"""
监控面板 FastAPI 应用

提供系统指标采集和历史数据查询 API
"""
import asyncio
import platform
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from app.metrics import collect_metrics, collect_metrics_with_rate, get_system_info
from app.database import init_database, save_metrics, get_history_metrics, clean_old_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化数据库
    init_database()
    print("[启动] 数据库初始化完成")

    # 启动时采集一次
    metrics = collect_metrics()
    save_metrics(metrics)
    print("[启动] 初始指标采集完成")

    # 启动后台任务
    collect_task = asyncio.create_task(periodic_collect())
    cleanup_task = asyncio.create_task(periodic_cleanup())

    print("[启动] 监控服务已启动，端口 3001")
    yield

    # 关闭时取消任务
    collect_task.cancel()
    cleanup_task.cancel()
    try:
        await collect_task
        await cleanup_task
    except asyncio.CancelledError:
        pass
    print("[关闭] 服务已停止")


async def periodic_collect():
    """定时采集指标（每30秒）"""
    while True:
        try:
            await asyncio.sleep(30)
            metrics = collect_metrics_with_rate()
            save_metrics(metrics)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[错误] 采集指标失败: {e}")


async def periodic_cleanup():
    """定时清理旧数据（每小时）"""
    while True:
        try:
            await asyncio.sleep(3600)
            clean_old_data(days=7)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[错误] 清理数据失败: {e}")


# 创建 FastAPI 应用
app = FastAPI(
    title="Server Monitor",
    description="轻量级服务器监控面板 API",
    version="1.0.0",
    lifespan=lifespan
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/metrics")
async def api_metrics():
    """获取实时系统指标"""
    return collect_metrics_with_rate()


@app.get("/api/metrics/history")
async def api_metrics_history(hours: int = Query(default=24, ge=1, le=168)):
    """获取历史指标数据"""
    return get_history_metrics(hours)


@app.get("/api/system/info")
async def api_system_info():
    """获取系统基本信息"""
    return get_system_info()


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "healthy"}
