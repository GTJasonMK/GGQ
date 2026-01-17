"""
Auth Service Configuration
从统一配置文件读取关键配置
"""
import os
import sys
from pathlib import Path

# 添加 backend 目录到路径，以便导入统一配置
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 导入统一配置
try:
    import config as unified_config
except ImportError:
    unified_config = None

# 基础路径
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# 数据库配置
DATABASE_URL = f"sqlite+aiosqlite:///{DATA_DIR}/auth.db"

# JWT配置
# 优先从统一配置读取，其次环境变量，最后使用默认值
_DEFAULT_JWT_SECRET = "dev_jwt_secret_key_for_local_development_only_change_in_production"
if unified_config and hasattr(unified_config, 'AUTH_JWT_SECRET'):
    JWT_SECRET_KEY = unified_config.AUTH_JWT_SECRET
else:
    JWT_SECRET_KEY = os.getenv("AUTH_JWT_SECRET", _DEFAULT_JWT_SECRET)
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7

# 密码配置
PASSWORD_MIN_LENGTH = 8
BCRYPT_ROUNDS = 12

# 邀请码配置
INVITE_CODE_LENGTH = 32
DEFAULT_INVITE_CODE_EXPIRE_DAYS = 7

# CORS配置
CORS_ORIGINS = [
    "http://localhost",
    "http://localhost:80",
    "http://127.0.0.1",
    "http://127.0.0.1:80",
    "http://localhost:5500",  # VS Code Live Server
    "http://127.0.0.1:5500",
]

# 角色定义
class UserRole:
    SUPER_ADMIN = 0  # 超级管理员
    ADMIN = 1        # 管理员
    USER = 2         # 普通用户

    @classmethod
    def get_name(cls, role: int) -> str:
        names = {0: "super_admin", 1: "admin", 2: "user"}
        return names.get(role, "unknown")

    @classmethod
    def can_manage(cls, operator_role: int, target_role: int) -> bool:
        """检查操作者是否可以管理目标角色"""
        # 超管可以管理所有人
        if operator_role == cls.SUPER_ADMIN:
            return True
        # 管理员只能管理普通用户
        if operator_role == cls.ADMIN and target_role == cls.USER:
            return True
        return False

# 初始超管配置（优先从统一配置读取）
if unified_config and hasattr(unified_config, 'AUTH_ADMIN_EMAIL'):
    INITIAL_ADMIN_EMAIL = unified_config.AUTH_ADMIN_EMAIL
else:
    INITIAL_ADMIN_EMAIL = os.getenv("AUTH_ADMIN_EMAIL", "admin@example.com")

if unified_config and hasattr(unified_config, 'AUTH_ADMIN_USERNAME'):
    INITIAL_ADMIN_USERNAME = unified_config.AUTH_ADMIN_USERNAME
else:
    INITIAL_ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")

if unified_config and hasattr(unified_config, 'AUTH_ADMIN_PASSWORD'):
    INITIAL_ADMIN_PASSWORD = unified_config.AUTH_ADMIN_PASSWORD
else:
    INITIAL_ADMIN_PASSWORD = os.getenv("AUTH_ADMIN_PASSWORD", "admin123456")

# 服务端口配置（优先从统一配置读取）
if unified_config and hasattr(unified_config, 'AUTH_HOST'):
    AUTH_HOST = unified_config.AUTH_HOST
else:
    AUTH_HOST = "0.0.0.0"

if unified_config and hasattr(unified_config, 'AUTH_PORT'):
    AUTH_PORT = unified_config.AUTH_PORT
else:
    AUTH_PORT = 8001

# 免邀请码注册的邮箱后缀列表
if unified_config and hasattr(unified_config, 'AUTH_ALLOWED_EMAIL_DOMAINS'):
    ALLOWED_EMAIL_DOMAINS = unified_config.AUTH_ALLOWED_EMAIL_DOMAINS
else:
    ALLOWED_EMAIL_DOMAINS = []


def is_email_domain_allowed(email: str) -> bool:
    """检查邮箱是否在允许的域名列表中"""
    if not ALLOWED_EMAIL_DOMAINS:
        return False
    email_lower = email.lower()
    for domain in ALLOWED_EMAIL_DOMAINS:
        if email_lower.endswith(domain.lower()):
            return True
    return False


# 邮箱验证配置
if unified_config and hasattr(unified_config, 'AUTH_EMAIL_VERIFICATION_ENABLED'):
    EMAIL_VERIFICATION_ENABLED = unified_config.AUTH_EMAIL_VERIFICATION_ENABLED
else:
    EMAIL_VERIFICATION_ENABLED = False

if unified_config and hasattr(unified_config, 'AUTH_VERIFICATION_CODE_EXPIRE_MINUTES'):
    VERIFICATION_CODE_EXPIRE_MINUTES = unified_config.AUTH_VERIFICATION_CODE_EXPIRE_MINUTES
else:
    VERIFICATION_CODE_EXPIRE_MINUTES = 10

# SMTP 邮件配置
if unified_config and hasattr(unified_config, 'SMTP_HOST'):
    SMTP_HOST = unified_config.SMTP_HOST
else:
    SMTP_HOST = os.getenv("SMTP_HOST", "")

if unified_config and hasattr(unified_config, 'SMTP_PORT'):
    SMTP_PORT = unified_config.SMTP_PORT
else:
    SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

if unified_config and hasattr(unified_config, 'SMTP_USER'):
    SMTP_USER = unified_config.SMTP_USER
else:
    SMTP_USER = os.getenv("SMTP_USER", "")

if unified_config and hasattr(unified_config, 'SMTP_PASSWORD'):
    SMTP_PASSWORD = unified_config.SMTP_PASSWORD
else:
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

if unified_config and hasattr(unified_config, 'SMTP_FROM_NAME'):
    SMTP_FROM_NAME = unified_config.SMTP_FROM_NAME
else:
    SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Auth Service")
