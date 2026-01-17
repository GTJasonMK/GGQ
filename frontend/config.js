/**
 * 前端配置文件
 * 由 backend/generate_frontend_config.py 脚本根据 backend/config.py 自动生成
 * 部署时运行: python backend/generate_frontend_config.py
 */
window.APP_CONFIG = {
    // GGM API 地址（空字符串表示使用相对路径，通过 nginx 代理）
    GGM_API: '',

    // Auth API 地址
    AUTH_API: '',

    // Monitor API 地址
    MONITOR_API: '',

    // 免邀请码注册的邮箱后缀列表
    ALLOWED_EMAIL_DOMAINS: ["@mail.imggb.top"],

    // 辅助函数：获取 API 地址
    // 空字符串是有效值（Docker 部署），只有 undefined 才使用默认值
    getGGMApi: function() {
        return this.GGM_API !== undefined ? this.GGM_API : 'http://localhost:8000';
    },
    getAuthApi: function() {
        return this.AUTH_API !== undefined ? this.AUTH_API : 'http://localhost:8001';
    },
    getMonitorApi: function() {
        return this.MONITOR_API !== undefined ? this.MONITOR_API : 'http://localhost:3001';
    },
    // 检查邮箱是否在允许的域名列表中
    isEmailDomainAllowed: function(email) {
        if (!this.ALLOWED_EMAIL_DOMAINS || this.ALLOWED_EMAIL_DOMAINS.length === 0) {
            return false;
        }
        const emailLower = email.toLowerCase();
        return this.ALLOWED_EMAIL_DOMAINS.some(domain =>
            emailLower.endsWith(domain.toLowerCase())
        );
    }
};
