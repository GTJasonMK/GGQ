"""
前端配置生成脚本
根据 backend/config.py 生成 frontend/config.js

用法: python generate_frontend_config.py
"""
import sys
import json
from pathlib import Path

# 添加当前目录到路径
BACKEND_DIR = Path(__file__).parent
sys.path.insert(0, str(BACKEND_DIR))

import config

FRONTEND_DIR = BACKEND_DIR.parent / "frontend"
CONFIG_JS = FRONTEND_DIR / "config.js"

TEMPLATE = """/**
 * 前端配置文件
 * 由 backend/generate_frontend_config.py 脚本根据 backend/config.py 自动生成
 * 部署时运行: python backend/generate_frontend_config.py
 */
window.APP_CONFIG = {{
    // GGM API 地址（空字符串表示使用相对路径，通过 nginx 代理）
    GGM_API: '{ggm_api}',

    // Auth API 地址
    AUTH_API: '{auth_api}',

    // Monitor API 地址
    MONITOR_API: '{monitor_api}',

    // 免邀请码注册的邮箱后缀列表
    ALLOWED_EMAIL_DOMAINS: {allowed_domains},

    // 辅助函数：获取 API 地址
    // 空字符串是有效值（Docker 部署），只有 undefined 才使用默认值
    getGGMApi: function() {{
        return this.GGM_API !== undefined ? this.GGM_API : 'http://localhost:8000';
    }},
    getAuthApi: function() {{
        return this.AUTH_API !== undefined ? this.AUTH_API : 'http://localhost:8001';
    }},
    getMonitorApi: function() {{
        return this.MONITOR_API !== undefined ? this.MONITOR_API : 'http://localhost:3001';
    }},
    // 检查邮箱是否在允许的域名列表中
    isEmailDomainAllowed: function(email) {{
        if (!this.ALLOWED_EMAIL_DOMAINS || this.ALLOWED_EMAIL_DOMAINS.length === 0) {{
            return false;
        }}
        const emailLower = email.toLowerCase();
        return this.ALLOWED_EMAIL_DOMAINS.some(domain =>
            emailLower.endsWith(domain.toLowerCase())
        );
    }}
}};
"""

def main():
    ggm_api = getattr(config, 'FRONTEND_GGM_API', 'http://localhost:8000')
    auth_api = getattr(config, 'FRONTEND_AUTH_API', 'http://localhost:8001')
    monitor_api = getattr(config, 'FRONTEND_MONITOR_API', 'http://localhost:3001')
    allowed_domains = getattr(config, 'AUTH_ALLOWED_EMAIL_DOMAINS', [])

    content = TEMPLATE.format(
        ggm_api=ggm_api,
        auth_api=auth_api,
        monitor_api=monitor_api,
        allowed_domains=json.dumps(allowed_domains)
    )

    CONFIG_JS.write_text(content, encoding='utf-8')
    print(f"Generated: {CONFIG_JS}")
    print(f"  GGM_API: {ggm_api or '(empty - use relative path)'}")
    print(f"  AUTH_API: {auth_api or '(empty - use relative path)'}")
    print(f"  MONITOR_API: {monitor_api or '(empty - use relative path)'}")
    print(f"  ALLOWED_EMAIL_DOMAINS: {allowed_domains or '(empty - all need invite code)'}")

if __name__ == "__main__":
    main()
