/**
 * Auth Client Library
 * 统一认证客户端，供所有前端项目使用
 */
const AuthClient = {
    // API 基础地址（从全局配置读取，空字符串表示使用相对路径）
    get API_BASE() {
        if (window.APP_CONFIG) {
            return window.APP_CONFIG.getAuthApi();
        }
        // 未加载配置时：本地开发走 localhost，生产环境走相对路径
        const host = window.location.hostname;
        if (host === 'localhost' || host === '127.0.0.1') {
            return 'http://localhost:8001';
        }
        return '';
    },

    // 存储键名
    STORAGE_KEYS: {
        ACCESS_TOKEN: 'auth_access_token',
        REFRESH_TOKEN: 'auth_refresh_token',
        USER: 'auth_user'
    },

    // 角色常量
    ROLES: {
        SUPER_ADMIN: 0,
        ADMIN: 1,
        USER: 2
    },

    // Token刷新定时器
    _refreshTimer: null,

    /**
     * 初始化 - 页面加载时调用
     */
    async init() {
        // 如果有token，设置自动刷新
        if (this.getAccessToken()) {
            this.setupAutoRefresh();
        }
    },

    /**
     * 用户注册
     */
    async register(email, username, password, inviteCode) {
        const body = {
            email,
            username,
            password
        };

        // 只有在有邀请码时才添加该字段
        if (inviteCode) {
            body.invite_code = inviteCode;
        }

        const response = await fetch(`${this.API_BASE}/api/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || '注册失败');
        }

        this.setTokens(data.access_token, data.refresh_token);
        this.setUser(data.user);
        this.setupAutoRefresh();

        return data;
    },

    /**
     * 用户登录
     */
    async login(emailOrUsername, password) {
        const response = await fetch(`${this.API_BASE}/api/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email_or_username: emailOrUsername,
                password
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || '登录失败');
        }

        this.setTokens(data.access_token, data.refresh_token);
        this.setUser(data.user);
        this.setupAutoRefresh();

        return data;
    },

    /**
     * 用户登出
     */
    async logout() {
        const refreshToken = this.getRefreshToken();

        if (refreshToken) {
            try {
                await fetch(`${this.API_BASE}/api/auth/logout`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${this.getAccessToken()}`
                    },
                    body: JSON.stringify({ refresh_token: refreshToken })
                });
            } catch (e) {
                console.error('Logout request failed:', e);
            }
        }

        this.clearTokens();
        this.clearRefreshTimer();
    },

    /**
     * 刷新Token
     */
    async refreshToken() {
        const refreshToken = this.getRefreshToken();

        if (!refreshToken) {
            throw new Error('No refresh token');
        }

        const response = await fetch(`${this.API_BASE}/api/auth/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refreshToken })
        });

        const data = await response.json();

        if (!response.ok) {
            this.clearTokens();
            throw new Error(data.detail || '刷新令牌失败');
        }

        this.setTokens(data.access_token, data.refresh_token);
        return data;
    },

    /**
     * 获取当前用户信息
     */
    async getMe() {
        const response = await this.authFetch(`${this.API_BASE}/api/auth/me`);

        if (!response.ok) {
            throw new Error('获取用户信息失败');
        }

        const user = await response.json();
        this.setUser(user);
        return user;
    },

    /**
     * 修改密码
     */
    async changePassword(currentPassword, newPassword) {
        const response = await this.authFetch(`${this.API_BASE}/api/auth/password`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                current_password: currentPassword,
                new_password: newPassword
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || '修改密码失败');
        }

        // 密码修改后需要重新登录
        this.clearTokens();
        return data;
    },

    /**
     * 验证邀请码
     */
    async validateInviteCode(code) {
        const response = await fetch(`${this.API_BASE}/api/invite-codes/validate/${encodeURIComponent(code)}`);
        return await response.json();
    },

    /**
     * 带认证的fetch请求
     */
    async authFetch(url, options = {}) {
        const token = this.getAccessToken();

        if (!token) {
            throw new Error('未登录');
        }

        const headers = {
            ...options.headers,
            'Authorization': `Bearer ${token}`
        };

        let response = await fetch(url, { ...options, headers });

        // 如果token过期，尝试刷新
        if (response.status === 401) {
            try {
                await this.refreshToken();
                headers['Authorization'] = `Bearer ${this.getAccessToken()}`;
                response = await fetch(url, { ...options, headers });
            } catch (e) {
                this.clearTokens();
                throw new Error('会话已过期，请重新登录');
            }
        }

        return response;
    },

    /**
     * 获取访问令牌
     */
    getAccessToken() {
        return localStorage.getItem(this.STORAGE_KEYS.ACCESS_TOKEN);
    },

    /**
     * 获取刷新令牌
     */
    getRefreshToken() {
        return localStorage.getItem(this.STORAGE_KEYS.REFRESH_TOKEN);
    },

    /**
     * 获取缓存的用户信息
     */
    getUser() {
        const userStr = localStorage.getItem(this.STORAGE_KEYS.USER);
        return userStr ? JSON.parse(userStr) : null;
    },

    /**
     * 设置令牌
     */
    setTokens(accessToken, refreshToken) {
        localStorage.setItem(this.STORAGE_KEYS.ACCESS_TOKEN, accessToken);
        localStorage.setItem(this.STORAGE_KEYS.REFRESH_TOKEN, refreshToken);
    },

    /**
     * 设置用户信息
     */
    setUser(user) {
        localStorage.setItem(this.STORAGE_KEYS.USER, JSON.stringify(user));
    },

    /**
     * 清除所有认证数据
     */
    clearTokens() {
        localStorage.removeItem(this.STORAGE_KEYS.ACCESS_TOKEN);
        localStorage.removeItem(this.STORAGE_KEYS.REFRESH_TOKEN);
        localStorage.removeItem(this.STORAGE_KEYS.USER);
    },

    /**
     * 检查是否已登录
     */
    isAuthenticated() {
        return !!this.getAccessToken();
    },

    /**
     * 检查是否有指定最低角色
     */
    hasRole(minRole) {
        const user = this.getUser();
        return user && user.role <= minRole;
    },

    /**
     * 是否是管理员（包括超管）
     */
    isAdmin() {
        return this.hasRole(this.ROLES.ADMIN);
    },

    /**
     * 是否是超级管理员
     */
    isSuperAdmin() {
        return this.hasRole(this.ROLES.SUPER_ADMIN);
    },

    /**
     * 设置自动刷新Token
     */
    setupAutoRefresh() {
        this.clearRefreshTimer();

        // 每12分钟刷新一次（Token有效期15分钟）
        this._refreshTimer = setInterval(async () => {
            try {
                await this.refreshToken();
                console.log('Token refreshed automatically');
            } catch (e) {
                console.error('Auto refresh failed:', e);
                this.clearRefreshTimer();
            }
        }, 12 * 60 * 1000);
    },

    /**
     * 清除刷新定时器
     */
    clearRefreshTimer() {
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
            this._refreshTimer = null;
        }
    },

    /**
     * 跳转到登录页
     */
    redirectToLogin(returnUrl = window.location.href) {
        const loginUrl = this.getAuthPageUrl('login.html');
        window.location.href = `${loginUrl}?redirect=${encodeURIComponent(returnUrl)}`;
    },

    /**
     * 获取认证页面URL（相对于当前页面）
     */
    getAuthPageUrl(page) {
        // 尝试找到auth目录的相对路径
        const path = window.location.pathname;

        if (path.includes('/GGM/')) {
            return `../auth/${page}`;
        } else if (path.includes('/admin/')) {
            return `../auth/${page}`;
        } else if (path.includes('/monitoringDashboard/')) {
            return `../auth/${page}`;
        } else {
            return `auth/${page}`;
        }
    },

    /**
     * 要求登录的页面守卫
     */
    async requireAuth() {
        if (!this.isAuthenticated()) {
            this.redirectToLogin();
            return false;
        }

        const cachedUser = this.getUser();
        if (cachedUser) {
            return true;
        }

        try {
            await this.getMe();
            return true;
        } catch (e) {
            this.redirectToLogin();
            return false;
        }
    },

    /**
     * 要求管理员权限的页面守卫
     */
    async requireAdmin() {
        const authenticated = await this.requireAuth();
        if (!authenticated) return false;

        if (!this.isAdmin()) {
            alert('需要管理员权限');
            window.location.href = '/';
            return false;
        }

        return true;
    },

    /**
     * 发送邮箱验证码
     */
    async sendVerificationCode(email) {
        const response = await fetch(`${this.API_BASE}/api/auth/verification/send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || '发送验证码失败');
        }

        return data;
    },

    /**
     * 使用验证码注册
     */
    async registerWithVerification(email, username, password, verificationCode) {
        const response = await fetch(`${this.API_BASE}/api/auth/register-with-verification`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email,
                username,
                password,
                verification_code: verificationCode
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || '注册失败');
        }

        this.setTokens(data.access_token, data.refresh_token);
        this.setUser(data.user);
        this.setupAutoRefresh();

        return data;
    },

    /**
     * 获取验证功能状态
     */
    async getVerificationStatus() {
        try {
            const response = await fetch(`${this.API_BASE}/api/auth/verification/status`);
            return await response.json();
        } catch (e) {
            return { enabled: false, email_configured: false };
        }
    }
};

// 页面加载时初始化
if (typeof window !== 'undefined') {
    window.AuthClient = AuthClient;
    AuthClient.init();
}
