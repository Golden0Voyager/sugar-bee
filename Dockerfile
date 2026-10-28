# Sugar Bee - 蜜蜂控糖
# 生产环境 Docker 镜像

FROM python:3.12-slim

LABEL maintainer="Haining Yu"
LABEL description="Sugar Bee - Glucose Tracker Application"

WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/app

# 安装系统依赖（清理缓存减小镜像体积）
RUN sed -i "s|deb.debian.org|mirrors.tencent.com|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null; apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# 先复制依赖文件并安装（利用 Docker 缓存层）
# 默认走腾讯镜像（本机 make build 快）；境外构建（如 GitHub Actions）用
# --build-arg PIP_INDEX_URL=https://pypi.org/simple 覆盖，避免镜像源断流。
ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 --timeout 60 \
    -i "$PIP_INDEX_URL" -r requirements.txt gunicorn google-cloud-storage

# 复制应用代码
COPY . .

# 创建非 root 用户并设置权限
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && mkdir -p /app/static/avatars /app/backups /app/.garmin_tokens /app/user_data \
    && chown -R appuser:appuser /app

USER appuser

# 暴露端口
EXPOSE 5000

# 健康检查（使用 /login 页面作为可用性检查，因为它不需要认证）
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:5000/health > /dev/null || exit 1

# 生产环境启动（Gunicorn）
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:application"]
