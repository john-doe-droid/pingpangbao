# ============================================================================
# Dockerfile —— 多阶段构建（构建前端静态资源 / 安装依赖 / 精简运行镜像）
#
# 说明：本项目当前只用 Django Templates + Bootstrap 5，没有前端构建步骤，
#      因此第一阶段（builder）只负责把 Python 依赖装进独立前缀，
#      运行阶段保持精简。
# ============================================================================

# ---------- 阶段 1：构建依赖 ----------
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 编译 psycopg 等可能需要的基础工具
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .

# 装到独立前缀，便于整体复制到运行阶段
RUN pip install --prefix=/install -r requirements.txt


# ---------- 阶段 2：运行 ----------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    PATH="/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"

# 运行期只需要 PostgreSQL 客户端库；不要编译器
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户运行（降低容器逃逸后的影响面）
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home app

COPY --from=builder /install /usr/local

WORKDIR /app

# 只复制运行所需内容（.dockerignore 已排除 .env / .venv / 日志等）
COPY --chown=app:app manage.py ./
COPY --chown=app:app config ./config
COPY --chown=app:app accounts ./accounts
COPY --chown=app:app core ./core
COPY --chown=app:app templates ./templates
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app deploy ./deploy
COPY --chown=app:app requirements.txt ./

# 静态资源目录与日志目录（collectstatic 与运行期写入需要）
RUN mkdir -p /app/staticfiles /app/media /app/logs \
    && chown -R app:app /app

USER app

# 收集静态资源。
# 使用 build 配置：collectstatic 不需要数据库与真实密钥，
# 而 prod 配置会在导入时校验它们（构建阶段不存在），直接沿用会导致构建失败。
# 运行阶段仍使用 prod 配置（见 docker-compose 的 DJANGO_SETTINGS_MODULE）。
RUN DJANGO_SETTINGS_MODULE=config.settings.build \
    python manage.py collectstatic --noinput --clear

EXPOSE 8000

# 健康检查：就绪探针（含数据库）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/ready || exit 1

# 入口：先跑迁移，再用 Gunicorn 启动（迁移幂等，可安全重复执行）
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application -c deploy/gunicorn.conf.py"]
