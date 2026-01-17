# WebDev - Gemini API 代理与服务管理平台

一个集成了 Gemini API 代理、用户认证、服务器监控的全栈 Web 应用。

## 功能特性

### GGM (Gemini Gateway Manager)
- OpenAI 兼容 API（可直接对接 ChatGPT 客户端）
- 账号池管理，自动轮换账号
- 凭证自动刷新（基于 Playwright 浏览器自动化）
- 账号健康检查与自动替换
- 文件上传支持（图片分析）

### Auth 认证服务
- JWT Token 认证（Access Token + Refresh Token）
- 角色权限控制（超级管理员、管理员、普通用户）
- 邀请码注册机制

### Monitor 监控服务
- 服务器 CPU、内存、磁盘实时监控
- 历史数据记录与图表展示

## 项目结构

```
webdev/
├── backend/
│   ├── GGM/                    # Gemini API 代理服务 (端口 8000)
│   │   ├── app/
│   │   │   ├── api/            # API 路由
│   │   │   ├── services/       # 业务逻辑
│   │   │   └── models/         # 数据模型
│   │   ├── config.json         # 账号配置
│   │   └── credient.txt        # 凭证文件
│   ├── auth/                   # 认证服务 (端口 8001)
│   ├── monitoringDashboard/    # 监控服务 (端口 3001)
│   ├── config.py               # 统一配置文件
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── index.html              # 门户首页
│   ├── auth/                   # 登录/注册页面
│   ├── GGM/                    # GGM 管理界面
│   ├── monitoringDashboard/    # 监控仪表盘
│   └── config.js               # 前端配置
├── docker-compose.yml
├── nginx.conf
└── README.md
```

## 快速部署 (Docker)

### 1. 克隆项目

```bash
git clone <repository-url>
cd webdev
```

### 2. 修改配置

编辑 `backend/config.py`：

```python
# 必须修改的配置
GGM_ADMIN_PASSWORD = "你的管理员密码"
GGM_ADMIN_SECRET_KEY = "随机字符串"
GGM_API_TOKENS = ["你的API密钥"]

AUTH_JWT_SECRET = "至少32位的随机字符串"
AUTH_ADMIN_PASSWORD = "认证服务管理员密码"

# QQ 邮箱配置（用于接收 Google 验证码）
QQ_EMAIL_ADDRESS = "你的QQ邮箱"
QQ_EMAIL_AUTH_CODE = "QQ邮箱授权码"

# 如果服务器无法直连 Google，配置代理
GGM_PROXY = "http://127.0.0.1:7890"
```

### 3. 添加初始账号

编辑 `backend/GGM/credient.txt`，每行一个邮箱：

```
account1@example.com
account2@example.com
```

### 4. 构建并启动

```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose --compatibility up -d

# 查看日志
docker-compose logs -f
```

### 5. 访问服务

- 前端门户: http://your-server-ip
- GGM API: http://your-server-ip/api/ggm/
- Auth API: http://your-server-ip/api/auth/
- Monitor API: http://your-server-ip/api/monitor/

## 配置说明

### 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| GGM | 8000 | Gemini API 代理 |
| Auth | 8001 | 认证服务 |
| Monitor | 3001 | 监控服务 |
| Frontend | 80 | Nginx 前端 |

### 账号池配置

```python
# backend/config.py

ACCOUNT_POOL_TARGET_COUNT = 35          # 目标账号数量
ACCOUNT_POOL_HEALTH_CHECK_INTERVAL = 300  # 健康检查间隔（秒）
ACCOUNT_POOL_MAX_REFRESH_FAILURES = 2   # 最大刷新失败次数
ACCOUNT_POOL_MAX_CONSECUTIVE_ERRORS = 3 # 最大连续错误次数
ACCOUNT_POOL_CREDENTIAL_EXPIRE_HOURS = 12  # 凭证过期时间（小时）
ACCOUNT_POOL_MAX_CONCURRENT = 1         # 最大并发注册数（2G内存建议设为1）
```

### 自动登录配置

```python
AUTO_LOGIN_ENABLED = True               # 启用自动凭证刷新
AUTO_LOGIN_HEADLESS = True              # 无头浏览器模式（Docker必须为True）
YESCAPTCHA_API_KEY = ""                 # YesCaptcha API（可选，用于验证码）
```

## API 文档

### GGM API

#### 聊天完成（OpenAI 兼容）

```bash
POST /v1/chat/completions
Authorization: Bearer <your-api-token>
Content-Type: application/json

{
  "model": "gemini-2.0-flash-exp",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ]
}
```

#### 获取模型列表

```bash
GET /v1/models
Authorization: Bearer <your-api-token>
```

#### 文件上传

```bash
POST /v1/files
Authorization: Bearer <your-api-token>
Content-Type: multipart/form-data

file: <image-file>
```

#### 健康检查

```bash
GET /health
```

### Auth API

#### 登录

```bash
POST /api/auth/login
Content-Type: application/json

{
  "username": "admin",
  "password": "password"
}
```

#### 注册（需要邀请码）

```bash
POST /api/auth/register
Content-Type: application/json

{
  "username": "newuser",
  "email": "user@example.com",
  "password": "password",
  "invite_code": "INVITE123"
}
```

#### 刷新 Token

```bash
POST /api/auth/refresh
Content-Type: application/json

{
  "refresh_token": "<refresh-token>"
}
```

## 本地开发

### 后端

```bash
cd backend

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium

# 启动 GGM 服务
python GGM/run.py

# 启动 Auth 服务
python auth/run.py

# 启动 Monitor 服务
python monitoringDashboard/run.py
```

### 前端

前端是纯静态文件，使用任意 HTTP 服务器：

```bash
# 使用 Python
cd frontend
python -m http.server 5500

# 或使用 VS Code Live Server 插件
```

本地开发时修改 `frontend/config.js`：

```javascript
window.APP_CONFIG = {
    GGM_API: 'http://localhost:8000',
    AUTH_API: 'http://localhost:8001',
    MONITOR_API: 'http://localhost:3001',
    // ...
};
```

## 运维命令

### Docker 常用命令

```bash
# 查看容器状态
docker-compose ps

# 查看日志
docker-compose logs -f ggm

# 重启服务（配置修改后）
docker-compose restart ggm

# 查看资源占用
docker stats

# 停止所有服务
docker-compose down

# 重新构建并启动
docker-compose build && docker-compose --compatibility up -d
```

### 账号管理

```bash
# 查看账号状态（通过 API）
curl -H "Authorization: Bearer <admin-token>" http://localhost:8000/api/admin/status

# 手动补充账号（本地运行）
cd backend
python sync_accounts.py --target 35 --server root@your-server-ip
```

## 内存占用参考

| 服务 | 正常运行 | Playwright 运行时 |
|------|---------|------------------|
| GGM | ~100MB | ~400MB (单个浏览器实例) |
| Auth | ~50MB | - |
| Monitor | ~40MB | - |
| Nginx | ~5MB | - |

2G 内存服务器建议：
- `ACCOUNT_POOL_MAX_CONCURRENT = 1`
- GGM 内存限制设为 1024MB

## 故障排查

### 账号池不自动补充

1. 检查 `AUTO_LOGIN_ENABLED` 是否为 `True`
2. 检查 QQ 邮箱配置是否正确
3. 查看日志：`docker-compose logs -f ggm`

### API 返回 401

1. 检查 `GGM_API_TOKENS` 配置
2. 确认请求头 `Authorization: Bearer <token>` 格式正确

### 浏览器启动失败

1. Docker 中确保 `AUTO_LOGIN_HEADLESS = True`
2. 检查 Playwright 依赖是否安装完整
3. 查看详细错误日志

## 许可证

MIT License
