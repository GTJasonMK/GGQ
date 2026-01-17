"""
Auth Service Runner
从统一配置读取端口
"""
import uvicorn
from app.config import AUTH_HOST, AUTH_PORT

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=AUTH_HOST,
        port=AUTH_PORT,
        reload=False,
        log_level="info"
    )
