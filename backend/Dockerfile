# 使用 Playwright 官方镜像（已包含浏览器和依赖，无需下载）
# 相比 python:3.11-slim，这个镜像大一点，但构建速度快很多
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# 设置工作目录
WORKDIR /app

# 替换 apt 源为阿里云 (加速后续可能的安装)
RUN sed -i 's/archive.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    sed -i 's/security.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
# 增加国内源加速
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# Playwright 浏览器已经包含在基础镜像中，不需要再运行 install
# 只需要确保路径正确

# 复制应用代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/GGM/data /app/auth/data /app/monitoringDashboard/data

# 默认端口
EXPOSE 8000 8001 3001

# 默认命令
CMD ["python", "-u", "GGM/run.py"]
