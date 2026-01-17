# 后端服务基础镜像（带 Playwright 支持）
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装 Playwright 浏览器所需的系统依赖
# 参考: https://playwright.dev/python/docs/browsers#install-system-dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 基础编译依赖
    gcc \
    libffi-dev \
    curl \
    # Chromium 浏览器依赖
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    libxshmfence1 \
    # 字体支持
    fonts-liberation \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 安装 Playwright 浏览器（只安装 Chromium 节省空间）
RUN playwright install chromium

# 复制应用代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/GGM/data /app/auth/data /app/monitoringDashboard/data

# 默认端口（可被覆盖）
EXPOSE 8000 8001 3001

# 默认命令（由 docker-compose 覆盖）
CMD ["python", "-u", "GGM/run.py"]
