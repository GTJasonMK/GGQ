/**
 * Internationalization (i18n) Module
 * 国际化模块 - 支持中英双语
 */
const I18n = {
    // 当前语言
    currentLang: 'zh',

    // 存储键名
    STORAGE_KEY: 'app_language',

    // 翻译数据
    translations: {
        // 通用
        common: {
            en: {
                appName: 'Portal',
                login: 'Sign In',
                logout: 'Logout',
                register: 'Register',
                submit: 'Submit',
                cancel: 'Cancel',
                save: 'Save',
                delete: 'Delete',
                edit: 'Edit',
                create: 'Create',
                search: 'Search',
                loading: 'Loading...',
                error: 'Error',
                success: 'Success',
                confirm: 'Confirm',
                back: 'Back',
                next: 'Next',
                previous: 'Previous',
                actions: 'Actions',
                status: 'Status',
                active: 'Active',
                disabled: 'Disabled',
                enabled: 'Enabled',
                expired: 'Expired',
                never: 'Never',
                unlimited: 'Unlimited',
                copy: 'Copy',
                copied: 'Copied',
                usedUp: 'Used Up',
                required: 'Required',
                optional: 'Optional',
                yes: 'Yes',
                no: 'No',
                all: 'All',
                none: 'None',
                language: 'Language',
                switchLang: 'Switch Language'
            },
            zh: {
                appName: '门户',
                login: '登录',
                logout: '退出登录',
                register: '注册',
                submit: '提交',
                cancel: '取消',
                save: '保存',
                delete: '删除',
                edit: '编辑',
                create: '创建',
                search: '搜索',
                loading: '加载中...',
                error: '错误',
                success: '成功',
                confirm: '确认',
                back: '返回',
                next: '下一步',
                previous: '上一步',
                actions: '操作',
                status: '状态',
                active: '活跃',
                disabled: '已禁用',
                enabled: '已启用',
                expired: '已过期',
                never: '永不',
                unlimited: '无限制',
                copy: '复制',
                copied: '已复制',
                usedUp: '已用完',
                required: '必填',
                optional: '可选',
                yes: '是',
                no: '否',
                all: '全部',
                none: '无',
                language: '语言',
                switchLang: '切换语言'
            }
        },

        // 认证相关
        auth: {
            en: {
                welcomeBack: 'Welcome Back',
                signInToContinue: 'Sign in to continue',
                createAccount: 'Create Account',
                registerWithInvite: 'Register with an invite code',
                emailOrUsername: 'Email or Username',
                email: 'Email',
                username: 'Username',
                password: 'Password',
                confirmPassword: 'Confirm Password',
                inviteCode: 'Invite Code',
                enterEmail: 'Enter your email',
                enterUsername: 'Enter your username',
                enterPassword: 'Enter your password',
                enterEmailOrUsername: 'Enter email or username',
                enterInviteCode: 'Enter your invite code',
                confirmYourPassword: 'Confirm your password',
                verifyCode: 'Verify Code',
                verifying: 'Verifying...',
                signingIn: 'Signing in...',
                creatingAccount: 'Creating account...',
                noAccount: "Don't have an account?",
                hasAccount: 'Already have an account?',
                registerWithCode: 'Register with invite code',
                backToPortal: 'Back to Portal',
                usernameHint: '3-50 characters, letters, numbers and underscore only',
                passwordHint: 'At least 8 characters with letters and numbers',
                inviteCodeValid: 'Invite code is valid!',
                registerAs: 'You will be registered as:',
                passwordMismatch: 'Passwords do not match',
                passwordWeak: 'Password must contain letters and numbers',
                accountCreated: 'Account created successfully! Redirecting...',
                invalidCredentials: 'Invalid credentials',
                loginFailed: 'Login failed',
                registerFailed: 'Registration failed',
                validateFailed: 'Failed to validate invite code'
            },
            zh: {
                welcomeBack: '欢迎回来',
                signInToContinue: '登录以继续',
                createAccount: '创建账户',
                registerWithInvite: '使用邀请码注册',
                emailOrUsername: '邮箱或用户名',
                email: '邮箱',
                username: '用户名',
                password: '密码',
                confirmPassword: '确认密码',
                inviteCode: '邀请码',
                enterEmail: '请输入邮箱',
                enterUsername: '请输入用户名',
                enterPassword: '请输入密码',
                enterEmailOrUsername: '请输入邮箱或用户名',
                enterInviteCode: '请输入邀请码',
                confirmYourPassword: '请再次输入密码',
                verifyCode: '验证邀请码',
                verifying: '验证中...',
                signingIn: '登录中...',
                creatingAccount: '创建账户中...',
                noAccount: '还没有账户？',
                hasAccount: '已有账户？',
                registerWithCode: '使用邀请码注册',
                backToPortal: '返回门户',
                usernameHint: '3-50个字符，仅限字母、数字和下划线',
                passwordHint: '至少8个字符，包含字母和数字',
                inviteCodeValid: '邀请码有效！',
                registerAs: '您将注册为：',
                passwordMismatch: '两次输入的密码不一致',
                passwordWeak: '密码必须包含字母和数字',
                accountCreated: '账户创建成功！正在跳转...',
                invalidCredentials: '凭据无效',
                loginFailed: '登录失败',
                registerFailed: '注册失败',
                validateFailed: '验证邀请码失败'
            }
        },

        // 门户页面
        portal: {
            en: {
                welcomeToPortal: 'Welcome to Portal',
                selectProject: 'Select a project to get started',
                helloUser: 'Hello, {username}! Select a project to get started.',
                signInRequired: 'Sign In Required',
                signInToAccess: 'Please sign in to access the projects and tools.',
                chatTitle: 'Chat',
                chatDesc: 'AI Chat interface powered by Gemini. Start conversations with advanced AI models.',
                tokenTitle: 'API Token',
                tokenDesc: 'Apply for API access token to use Gemini services programmatically.',
                adminTitle: 'Admin Panel',
                adminDesc: 'User management, token review, and system administration tools.',
                adminOnly: 'Admin Only',
                open: 'Open'
            },
            zh: {
                welcomeToPortal: '欢迎来到门户',
                selectProject: '选择一个项目开始',
                helloUser: '你好，{username}！选择一个项目开始。',
                signInRequired: '需要登录',
                signInToAccess: '请登录以访问项目和工具。',
                chatTitle: '聊天',
                chatDesc: '由 Gemini 驱动的 AI 聊天界面，与先进的 AI 模型对话。',
                tokenTitle: 'API 令牌',
                tokenDesc: '申请 API 访问令牌，以编程方式使用 Gemini 服务。',
                adminTitle: '管理面板',
                adminDesc: '用户管理、令牌审核和系统管理工具。',
                adminOnly: '仅管理员',
                open: '开放'
            }
        },

        // 管理面板
        admin: {
            en: {
                dashboard: 'Admin Dashboard',
                adminPanel: 'Admin Panel',
                selectModule: 'Select a management module',
                usageAnalytics: 'Usage Analytics',
                usageAnalyticsDesc: 'View usage statistics and trends',
                conversationBrowser: 'Conversation Browser',
                conversationBrowserDesc: 'Browse and analyze user conversations',
                userManagement: 'User Management',
                userManagementDesc: 'Manage users, invite codes, and permissions',
                tokenReview: 'Token Review',
                tokenReviewDesc: 'Review and approve user token applications',
                ggmDesc: 'Manage Gemini accounts and API tokens',
                monitorDesc: 'View server metrics and performance',
                inviteCodes: 'Invite Codes',
                users: 'Users',
                allUsers: 'All Users',
                createUser: 'Create User',
                editUser: 'Edit User',
                deleteUser: 'Delete User',
                totalUsers: 'Total Users',
                activeUsers: 'Active Users',
                admins: 'Admins',
                newThisWeek: 'New This Week',
                user: 'User',
                role: 'Role',
                created: 'Created',
                lastLogin: 'Last Login',
                superAdmin: 'Super Admin',
                adminRole: 'Admin',
                userRole: 'User',
                searchPlaceholder: 'Search by username or email...',
                allRoles: 'All Roles',
                showing: 'Showing {start}-{end} of {total}',
                noUsers: 'No users found',
                confirmDeleteUser: 'Are you sure you want to delete user "{username}"?',
                userCreated: 'User created successfully',
                userUpdated: 'User updated successfully',
                userDeleted: 'User deleted successfully',
                backToPortal: 'Back to Portal',
                backToAdmin: 'Back to Admin',
                email: 'Email',
                username: 'Username',
                password: 'Password',
                saveChanges: 'Save Changes',
                previous: 'Previous',
                next: 'Next',

                // 邀请码
                manageInviteCodes: 'Manage Invite Codes',
                createCode: 'Create Code',
                code: 'Code',
                roleGrant: 'Role Grant',
                roleToGrant: 'Role to Grant',
                uses: 'Uses',
                expires: 'Expires',
                createdBy: 'Created By',
                maxUses: 'Max Uses',
                maxUsesHint: '0 = unlimited',
                expiresIn: 'Expires In',
                expiresInHint: 'days, 0 = never',
                usedUp: 'Used Up',
                noInviteCodes: 'No invite codes',
                confirmDeleteCode: 'Are you sure you want to delete this invite code?',
                codeCreated: 'Invite code created: {code}',
                codeDeleted: 'Invite code deleted',
                copyToClipboard: 'Copied to clipboard',
                unlimited: 'Unlimited',
                never: 'Never'
            },
            zh: {
                dashboard: '管理仪表板',
                adminPanel: '管理面板',
                selectModule: '选择管理模块',
                usageAnalytics: '使用分析',
                usageAnalyticsDesc: '查看使用统计和趋势',
                conversationBrowser: '会话浏览',
                conversationBrowserDesc: '浏览和分析用户会话',
                userManagement: '用户管理',
                userManagementDesc: '管理用户、邀请码和权限',
                tokenReview: '令牌审核',
                tokenReviewDesc: '审核和批准用户令牌申请',
                ggmDesc: '管理 Gemini 账号和 API 令牌',
                monitorDesc: '查看服务器指标和性能',
                inviteCodes: '邀请码',
                users: '用户',
                allUsers: '所有用户',
                createUser: '创建用户',
                editUser: '编辑用户',
                deleteUser: '删除用户',
                totalUsers: '用户总数',
                activeUsers: '活跃用户',
                admins: '管理员',
                newThisWeek: '本周新增',
                user: '用户',
                role: '角色',
                created: '创建时间',
                lastLogin: '最后登录',
                superAdmin: '超级管理员',
                adminRole: '管理员',
                userRole: '普通用户',
                searchPlaceholder: '按用户名或邮箱搜索...',
                allRoles: '所有角色',
                showing: '显示 {start}-{end}，共 {total}',
                noUsers: '未找到用户',
                confirmDeleteUser: '确定要删除用户 "{username}" 吗？',
                userCreated: '用户创建成功',
                userUpdated: '用户更新成功',
                userDeleted: '用户删除成功',
                backToPortal: '返回门户',
                backToAdmin: '返回管理',
                email: '邮箱',
                username: '用户名',
                password: '密码',
                saveChanges: '保存更改',
                previous: '上一页',
                next: '下一页',

                // 邀请码
                manageInviteCodes: '管理邀请码',
                createCode: '创建邀请码',
                code: '邀请码',
                roleGrant: '授予角色',
                roleToGrant: '授予角色',
                uses: '使用次数',
                expires: '过期时间',
                createdBy: '创建者',
                maxUses: '最大使用次数',
                maxUsesHint: '0 = 无限制',
                expiresIn: '有效期',
                expiresInHint: '天，0 = 永不过期',
                usedUp: '已用完',
                noInviteCodes: '暂无邀请码',
                confirmDeleteCode: '确定要删除这个邀请码吗？',
                codeCreated: '邀请码已创建：{code}',
                codeDeleted: '邀请码已删除',
                copyToClipboard: '已复制到剪贴板',
                unlimited: '无限制',
                never: '永不'
            }
        },

        // GGM 页面
        ggm: {
            en: {
                console: 'GGM Console',
                dashboard: 'Dashboard',
                accounts: 'Accounts',
                apiTokens: 'API Tokens',
                apiToken: 'API Token',
                enterApiToken: 'Enter API Token',
                enterToken: 'Enter admin password',
                connect: 'Connect',
                connecting: 'Connecting...',
                disconnect: 'Disconnect',
                invalidToken: 'Invalid password',
                connectionFailed: 'Connection failed',
                accountPool: 'Account Pool',
                availableAccounts: 'Available Accounts',
                totalAccounts: 'Total Accounts',
                activeAccounts: 'Active Accounts',
                errorAccounts: 'Error Accounts',
                refreshing: 'Refreshing',
                accountStatus: 'Account Status',
                accountIndex: 'Index',
                accountNote: 'Note',
                requestCount: 'Requests',
                errorCount: 'Errors',
                lastUsed: 'Last Used',
                status: 'Status',
                available: 'Available',
                unavailable: 'Unavailable',
                cooldown: 'Cooldown',
                refreshAccount: 'Refresh',
                apiUsage: 'API Usage',
                tokenUsage: 'Token Usage',
                requestsToday: 'Requests Today',
                tokensUsed: 'Tokens Used',
                chat: 'Chat',
                openChat: 'Open Chat',
                backToPortal: 'Back to Portal',
                avgScore: 'Avg Score',
                successRate: 'Success Rate',
                concurrent: 'Concurrent',
                accountOverview: 'Account Overview',
                allAccounts: 'All Accounts',
                autoRefresh: 'Auto refresh',
                refresh: 'Refresh',
                monitor: 'Monitor',
                portal: 'Portal'
            },
            zh: {
                console: 'GGM 控制台',
                dashboard: '仪表盘',
                accounts: '账号',
                apiTokens: 'API 令牌',
                apiToken: 'API 令牌',
                enterApiToken: '输入 API 令牌',
                enterToken: '输入管理员密码',
                connect: '连接',
                connecting: '连接中...',
                disconnect: '断开连接',
                invalidToken: '密码无效',
                connectionFailed: '连接失败',
                accountPool: '账号池',
                availableAccounts: '可用账号',
                totalAccounts: '账号总数',
                activeAccounts: '活跃账号',
                errorAccounts: '异常账号',
                refreshing: '刷新中',
                accountStatus: '账号状态',
                accountIndex: '序号',
                accountNote: '备注',
                requestCount: '请求数',
                errorCount: '错误数',
                lastUsed: '最后使用',
                status: '状态',
                available: '可用',
                unavailable: '不可用',
                cooldown: '冷却中',
                refreshAccount: '刷新',
                apiUsage: 'API 使用情况',
                tokenUsage: 'Token 使用量',
                requestsToday: '今日请求',
                tokensUsed: '已用 Token',
                chat: '聊天',
                openChat: '打开聊天',
                backToPortal: '返回门户',
                avgScore: '平均分',
                successRate: '成功率',
                concurrent: '并发数',
                accountOverview: '账号概览',
                allAccounts: '所有账号',
                autoRefresh: '自动刷新',
                refresh: '刷新',
                monitor: '监控',
                portal: '门户'
            }
        },

        // 监控面板
        monitor: {
            en: {
                serverMonitor: 'Server Monitor',
                backToPortal: 'Back to Portal',
                lastUpdated: 'Last updated',
                updating: 'Updating...',
                cpuUsage: 'CPU Usage',
                memoryUsage: 'Memory Usage',
                diskUsage: 'Disk Usage',
                networkIO: 'Network I/O',
                systemInfo: 'System Info',
                hostname: 'Hostname',
                os: 'OS',
                kernel: 'Kernel',
                uptime: 'Uptime',
                cpuCores: 'CPU Cores',
                totalMemory: 'Total Memory',
                totalDisk: 'Total Disk',
                processes: 'Processes',
                cpuHistory: 'CPU History',
                memoryHistory: 'Memory History',
                networkHistory: 'Network History',
                sent: 'Sent',
                received: 'Received',
                day: 'd',
                hour: 'h',
                minute: 'm',
                second: 's'
            },
            zh: {
                serverMonitor: '服务器监控',
                backToPortal: '返回门户',
                lastUpdated: '最后更新',
                updating: '更新中...',
                cpuUsage: 'CPU 使用率',
                memoryUsage: '内存使用率',
                diskUsage: '磁盘使用率',
                networkIO: '网络 I/O',
                systemInfo: '系统信息',
                hostname: '主机名',
                os: '操作系统',
                kernel: '内核',
                uptime: '运行时间',
                cpuCores: 'CPU 核心数',
                totalMemory: '总内存',
                totalDisk: '总磁盘',
                processes: '进程数',
                cpuHistory: 'CPU 历史',
                memoryHistory: '内存历史',
                networkHistory: '网络历史',
                sent: '发送',
                received: '接收',
                day: '天',
                hour: '时',
                minute: '分',
                second: '秒'
            }
        },

        // Token 申请页面
        token: {
            en: {
                apiToken: 'API Token',
                myToken: 'My API Token',
                applyToken: 'Apply for Token',
                reason: 'Application Reason (Optional)',
                submit: 'Submit Application',
                history: 'Application History',
                pending: 'Pending',
                approved: 'Approved',
                rejected: 'Rejected',
                noToken: 'No token yet',
                noApplications: 'No applications yet',
                submitSuccess: 'Application submitted successfully!',
                copied: 'Token copied!'
            },
            zh: {
                apiToken: 'API 令牌',
                myToken: '我的 API 令牌',
                applyToken: '申请令牌',
                reason: '申请理由（可选）',
                submit: '提交申请',
                history: '申请记录',
                pending: '待审核',
                approved: '已通过',
                rejected: '已拒绝',
                noToken: '暂无令牌',
                noApplications: '暂无申请记录',
                submitSuccess: '申请提交成功！',
                copied: '令牌已复制！'
            }
        },

        // 聊天页面
        chat: {
            en: {
                newChat: 'New Chat',
                clear: 'Clear',
                send: 'Send',
                typing: 'Typing...',
                welcome: 'Welcome to Gemini Chat',
                welcomeDesc: 'Start a conversation with AI'
            },
            zh: {
                newChat: '新对话',
                clear: '清空',
                send: '发送',
                typing: '输入中...',
                welcome: '欢迎使用 Gemini 聊天',
                welcomeDesc: '开始与 AI 对话'
            }
        },

        // 角色名称
        roles: {
            en: {
                0: 'Super Admin',
                1: 'Admin',
                2: 'User'
            },
            zh: {
                0: '超级管理员',
                1: '管理员',
                2: '普通用户'
            }
        }
    },

    /**
     * 初始化
     */
    init() {
        // 从localStorage读取语言设置
        const savedLang = localStorage.getItem(this.STORAGE_KEY);
        if (savedLang && (savedLang === 'en' || savedLang === 'zh')) {
            this.currentLang = savedLang;
        } else {
            // 根据浏览器语言自动选择
            const browserLang = navigator.language || navigator.userLanguage;
            this.currentLang = browserLang.startsWith('zh') ? 'zh' : 'en';
        }
        return this;
    },

    /**
     * 设置语言
     */
    setLang(lang) {
        if (lang === 'en' || lang === 'zh') {
            this.currentLang = lang;
            localStorage.setItem(this.STORAGE_KEY, lang);
            return true;
        }
        return false;
    },

    /**
     * 切换语言
     */
    toggleLang() {
        const newLang = this.currentLang === 'zh' ? 'en' : 'zh';
        this.setLang(newLang);
        return newLang;
    },

    /**
     * 获取当前语言
     */
    getLang() {
        return this.currentLang;
    },

    /**
     * 获取翻译文本
     * @param {string} category - 分类 (common, auth, portal, admin, roles)
     * @param {string} key - 键名
     * @param {object} params - 替换参数
     */
    t(category, key, params = {}) {
        const categoryData = this.translations[category];
        if (!categoryData) {
            console.warn(`I18n: Category "${category}" not found`);
            return key;
        }

        const langData = categoryData[this.currentLang];
        if (!langData) {
            console.warn(`I18n: Language "${this.currentLang}" not found in category "${category}"`);
            return key;
        }

        let text = langData[key];
        if (text === undefined) {
            console.warn(`I18n: Key "${key}" not found in category "${category}"`);
            return key;
        }

        // 替换参数
        Object.keys(params).forEach(param => {
            text = text.replace(new RegExp(`\\{${param}\\}`, 'g'), params[param]);
        });

        return text;
    },

    /**
     * 获取角色名称
     */
    getRoleName(role) {
        return this.translations.roles[this.currentLang][role] || role;
    },

    /**
     * 创建语言切换按钮
     */
    createLangSwitcher(className = '') {
        const btn = document.createElement('button');
        btn.className = `lang-switcher ${className}`;
        btn.innerHTML = this.currentLang === 'zh' ? 'EN' : '中';
        btn.title = this.t('common', 'switchLang');
        btn.onclick = () => {
            this.toggleLang();
            window.location.reload();
        };
        return btn;
    }
};

// 初始化
I18n.init();

// 导出
if (typeof window !== 'undefined') {
    window.I18n = I18n;
}
