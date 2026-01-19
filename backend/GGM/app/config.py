"""
配置管理模块
- 支持 JSON 配置文件
- 支持环境变量覆盖
- 配置热更新
- 从统一配置文件读取关键配置
"""
import os
import sys
import json
import secrets
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# 添加 backend 目录到路径，以便导入统一配置
BACKEND_DIR = Path(__file__).parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 导入统一配置
try:
    import config as unified_config
except ImportError:
    unified_config = None

logger = logging.getLogger(__name__)

# 项目根目录
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = BASE_DIR / "config.json"  # 配置文件在根目录
STATIC_DIR = BASE_DIR / "static"
IMAGES_DIR = DATA_DIR / "images"
CONVERSATIONS_DIR = DATA_DIR / "conversations"
API_SESSIONS_DIR = DATA_DIR / "api_sessions"  # 外部 API 一次性会话


class AccountConfig(BaseModel):
    """账号配置"""
    team_id: str = Field(..., description="团队ID (configId)")
    secure_c_ses: str = Field(..., description="__Secure-C_SES cookie")
    host_c_oses: str = Field("", description="__Host-C_OSES cookie")
    csesidx: str = Field(..., description="会话索引")
    user_agent: str = Field(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        description="User-Agent"
    )
    available: bool = Field(True, description="是否可用")
    note: str = Field("", description="备注")
    refresh_time: str = Field("", description="凭据刷新时间 (ISO 格式)")


class ModelConfig(BaseModel):
    """模型配置"""
    id: str
    name: str
    description: str = ""
    context_length: int = 32768
    max_tokens: int = 8192
    enabled: bool = True


class CooldownConfig(BaseModel):
    """冷却时间配置（秒）"""
    auth_error_seconds: int = Field(900, alias="auth_error", description="认证错误冷却时间")
    rate_limit_seconds: int = Field(300, alias="rate_limit", description="限额错误冷却时间")
    generic_error_seconds: int = Field(120, alias="generic_error", description="通用错误冷却时间")

    class Config:
        populate_by_name = True


class QQEmailConfig(BaseModel):
    """QQ 邮箱配置"""
    address: str = Field("", description="QQ 邮箱地址")
    auth_code: str = Field("", description="IMAP 授权码")
    imap_server: str = Field("imap.qq.com", description="IMAP 服务器")
    imap_port: int = Field(993, description="IMAP 端口")


class AutoLoginConfig(BaseModel):
    """自动登录配置"""
    enabled: bool = Field(False, description="是否启用自动登录")
    qq_email: QQEmailConfig = Field(default_factory=QQEmailConfig, description="QQ 邮箱配置")
    verification_timeout: int = Field(120, description="验证码等待超时（秒）")
    retry_count: int = Field(3, description="重试次数")
    headless: bool = Field(True, description="是否使用无头浏览器模式（False 可以看到浏览器界面，便于调试）")
    yescaptcha_api_key: str = Field("", description="YesCaptcha API 密钥（用于绕过 reCAPTCHA 验证）")


class AppConfig(BaseModel):
    """应用配置"""
    # 服务设置
    host: str = Field("0.0.0.0", description="监听地址")
    port: int = Field(8000, description="监听端口")

    # 代理设置
    proxy: str = Field("", description="HTTP代理地址")

    # 认证设置
    admin_password: str = Field("admin123", description="管理员密码")
    admin_secret_key: str = Field("", description="管理员密钥")
    admin_password_login_enabled: bool = Field(
        False,
        description="是否允许使用管理员密码登录（建议关闭以统一认证）"
    )
    api_tokens: List[str] = Field(default_factory=list, description="API访问令牌")

    # 账号列表
    accounts: List[AccountConfig] = Field(default_factory=list)

    # 模型列表
    models: List[ModelConfig] = Field(default_factory=lambda: [
        ModelConfig(id="gemini-2.5-flash", name="Gemini 2.5 Flash", description="快速响应"),
        ModelConfig(id="gemini-2.5-pro", name="Gemini 2.5 Pro", description="更强能力"),
        ModelConfig(id="gemini-3-pro", name="Gemini 3 Pro", description="最新模型"),
    ])

    # 冷却配置
    cooldown: CooldownConfig = Field(default_factory=CooldownConfig)

    # 自动登录配置
    auto_login: Optional[AutoLoginConfig] = Field(None, description="自动登录配置")

    # 凭证文件路径（用于批量账号管理）
    credentials_file: str = Field("credient.txt", description="邮箱账号列表文件路径")

    # 图片配置
    image_cache_hours: int = Field(24, description="图片缓存时间（小时）")
    image_base_url: str = Field("", description="图片访问基础URL")

    # 日志级别
    log_level: str = Field("INFO", description="日志级别")


class Settings(BaseSettings):
    """环境变量配置"""
    # 服务配置
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # 可以通过环境变量覆盖
    proxy: str = ""
    log_level: str = "INFO"

    class Config:
        env_prefix = "GEMINI_"
        env_file = ".env"
        extra = "ignore"


class ConfigManager:
    """配置管理器"""

    def __init__(self):
        self._config: Optional[AppConfig] = None
        self._settings = Settings()
        self._ensure_dirs()

    def _ensure_dirs(self):
        """确保目录存在"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
        API_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def load(self) -> AppConfig:
        """加载配置"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._config = AppConfig(**data)
                logger.info(f"配置已加载: {CONFIG_FILE}")
            except Exception as e:
                logger.error(f"加载配置失败: {e}")
                self._config = AppConfig()
        else:
            self._config = AppConfig()
            self.save()
            logger.info(f"已创建默认配置: {CONFIG_FILE}")

        # 从统一配置文件覆盖关键配置
        if unified_config:
            if hasattr(unified_config, 'GGM_HOST'):
                self._config.host = unified_config.GGM_HOST
            if hasattr(unified_config, 'GGM_PORT'):
                self._config.port = unified_config.GGM_PORT
            if hasattr(unified_config, 'GGM_ADMIN_PASSWORD'):
                self._config.admin_password = unified_config.GGM_ADMIN_PASSWORD
            if hasattr(unified_config, 'GGM_ADMIN_SECRET_KEY'):
                self._config.admin_secret_key = unified_config.GGM_ADMIN_SECRET_KEY
            if hasattr(unified_config, 'GGM_ADMIN_PASSWORD_LOGIN_ENABLED'):
                self._config.admin_password_login_enabled = (
                    unified_config.GGM_ADMIN_PASSWORD_LOGIN_ENABLED
                )
            if hasattr(unified_config, 'GGM_API_TOKENS'):
                self._config.api_tokens = unified_config.GGM_API_TOKENS
            if hasattr(unified_config, 'GGM_PROXY'):
                self._config.proxy = unified_config.GGM_PROXY
            # 自动登录配置
            if hasattr(unified_config, 'AUTO_LOGIN_ENABLED'):
                if self._config.auto_login is None:
                    self._config.auto_login = AutoLoginConfig()
                self._config.auto_login.enabled = unified_config.AUTO_LOGIN_ENABLED
            if hasattr(unified_config, 'QQ_EMAIL_ADDRESS') and unified_config.QQ_EMAIL_ADDRESS:
                if self._config.auto_login is None:
                    self._config.auto_login = AutoLoginConfig()
                self._config.auto_login.qq_email.address = unified_config.QQ_EMAIL_ADDRESS
                self._config.auto_login.qq_email.auth_code = getattr(unified_config, 'QQ_EMAIL_AUTH_CODE', '')
                self._config.auto_login.qq_email.imap_server = getattr(unified_config, 'QQ_EMAIL_IMAP_SERVER', 'imap.qq.com')
                self._config.auto_login.qq_email.imap_port = getattr(unified_config, 'QQ_EMAIL_IMAP_PORT', 993)
            if hasattr(unified_config, 'AUTO_LOGIN_HEADLESS'):
                if self._config.auto_login:
                    self._config.auto_login.headless = unified_config.AUTO_LOGIN_HEADLESS
            if hasattr(unified_config, 'YESCAPTCHA_API_KEY'):
                if self._config.auto_login:
                    self._config.auto_login.yescaptcha_api_key = unified_config.YESCAPTCHA_API_KEY
            logger.info("已从统一配置文件加载关键配置")

        # 环境变量覆盖
        if self._settings.proxy:
            self._config.proxy = self._settings.proxy
        if self._settings.log_level:
            self._config.log_level = self._settings.log_level

        # 确保有管理员密钥
        if not self._config.admin_secret_key:
            self._config.admin_secret_key = secrets.token_urlsafe(32)
            self.save()

        return self._config

    def save(self):
        """保存配置"""
        if self._config:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._config.model_dump(), f, indent=2, ensure_ascii=False)
            logger.debug("配置已保存")

    # 方法别名，兼容不同调用方式
    def load_config(self) -> AppConfig:
        """load 方法的别名"""
        return self.load()

    def save_config(self):
        """save 方法的别名"""
        self.save()

    @property
    def config(self) -> AppConfig:
        """获取配置"""
        if self._config is None:
            self.load()
        return self._config

    @property
    def settings(self) -> Settings:
        """获取环境变量配置"""
        return self._settings

    def get_account(self, index: int) -> Optional[AccountConfig]:
        """获取指定账号"""
        if 0 <= index < len(self.config.accounts):
            return self.config.accounts[index]
        return None

    def add_account(self, account: AccountConfig) -> int:
        """添加账号，返回索引"""
        self.config.accounts.append(account)
        self.save()
        return len(self.config.accounts) - 1

    def update_account(self, index: int, account: AccountConfig):
        """更新账号"""
        if 0 <= index < len(self.config.accounts):
            self.config.accounts[index] = account
            self.save()

    def remove_account(self, index: int) -> bool:
        """删除账号"""
        if 0 <= index < len(self.config.accounts):
            self.config.accounts.pop(index)
            self.save()
            return True
        return False


# 全局配置管理器
config_manager = ConfigManager()


def get_config() -> AppConfig:
    """获取应用配置"""
    return config_manager.config


def get_settings() -> Settings:
    """获取环境变量配置"""
    return config_manager.settings
