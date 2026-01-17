"""
轻量级服务器监控面板

主入口文件
从统一配置读取端口
"""
import sys
from pathlib import Path

# 添加 backend 目录到路径
BACKEND_DIR = Path(__file__).parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 导入统一配置
try:
    import config as unified_config
    MONITOR_HOST = getattr(unified_config, 'MONITOR_HOST', '0.0.0.0')
    MONITOR_PORT = getattr(unified_config, 'MONITOR_PORT', 3001)
except ImportError:
    MONITOR_HOST = "0.0.0.0"
    MONITOR_PORT = 3001

import uvicorn
from app.main import app

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=MONITOR_HOST,
        port=MONITOR_PORT,
        reload=False
    )
