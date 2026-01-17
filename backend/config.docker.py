"""
Docker 部署配置文件
复制此文件到 backend/config.py 覆盖原配置
"""
import os

# ============================================================
# 服务端口配置
# ============================================================
GGM_HOST = "0.0.0.0"
GGM_PORT = 8000

AUTH_HOST = "0.0.0.0"
AUTH_PORT = 8001

MONITOR_HOST = "0.0.0.0"
MONITOR_PORT = 3001

# ============================================================
# GGM 服务配置
# ============================================================
# 管理员密码（用于管理界面登录）
GGM_ADMIN_PASSWORD = "your_admin_password_here"

# 管理员密钥（用于生成管理员Token，必须是随机字符串）
GGM_ADMIN_SECRET_KEY = "your_random_secret_key_at_least_32_chars"

# 静态 API Token 列表（用于外部API调用）
GGM_API_TOKENS = ["your_api_token_here"]

# HTTP 代理（用于访问 Google API，如服务器可直连则留空）
# Docker 内部网络通常不需要代理
GGM_PROXY = ""

# ============================================================
# Auth 认证服务配置
# ============================================================
# JWT 密钥（必须修改为随机字符串，至少32位）
AUTH_JWT_SECRET = "your_jwt_secret_key_at_least_32_characters_long"

# 初始管理员账号（首次启动时自动创建）
AUTH_ADMIN_EMAIL = "admin@yourdomain.com"
AUTH_ADMIN_USERNAME = "admin"
AUTH_ADMIN_PASSWORD = "your_admin_password"

# ============================================================
# 前端 API 地址配置
# Docker 部署: 使用空字符串（通过 nginx 代理）
# ============================================================
FRONTEND_GGM_API = ""
FRONTEND_AUTH_API = ""
FRONTEND_MONITOR_API = ""

# ============================================================
# 自动登录配置（可选，用于自动刷新 Gemini 凭证）
# ============================================================
AUTO_LOGIN_ENABLED = False

# QQ 邮箱配置（用于接收 Google 验证码）
QQ_EMAIL_ADDRESS = ""
QQ_EMAIL_AUTH_CODE = ""
QQ_EMAIL_IMAP_SERVER = "imap.qq.com"
QQ_EMAIL_IMAP_PORT = 993

# 无头浏览器模式（Docker 中必须为 True）
AUTO_LOGIN_HEADLESS = True

# YesCaptcha API 密钥（用于绕过 reCAPTCHA，可选）
YESCAPTCHA_API_KEY = ""

# ============================================================
# 账号池管理配置
# ============================================================
ACCOUNT_POOL_TARGET_COUNT = 25
ACCOUNT_POOL_HEALTH_CHECK_INTERVAL = 300
ACCOUNT_POOL_MAX_REFRESH_FAILURES = 2
ACCOUNT_POOL_MAX_CONSECUTIVE_ERRORS = 3
ACCOUNT_POOL_CREDENTIAL_EXPIRE_HOURS = 12
ACCOUNT_POOL_MAX_CONCURRENT = 5
