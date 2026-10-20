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

# 运行期依赖。
#
# 【依据：对 Pillow 13.0.0 的 manylinux wheel 做了 ELF 依赖检查，非猜测】
#   pillow-13.0.0-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
#
#   * wheel **自带**以下库（放在 pillow.libs/ 内并 rpath 引用）：
#       libjpeg-*.so.62.4.0   libtiff-*.so.6.3.0    libopenjp2-*.so.2.5.4
#       libwebp-*.so.7.2.0    libpng16-*.so.16.58.0 libfreetype / harfbuzz / lcms2 …
#     ⇒ 因此**不需要**安装 libjpeg62-turbo（装了也不会被用到，属于多余依赖）。
#
#   * PIL/_imaging*.so 的 DT_NEEDED 里存在 `libz.so.1`，而 wheel **未自带** zlib
#     ⇒ 必须由系统提供 ⇒ 安装 zlib1g。
#     （JPEG 编码依赖 zlib；缺它会在容器内报 ImportError/解码失败，
#       而本地 Windows 版 Pillow 自带 zlib，所以本地测不出来。）
#
#   * libpq5：psycopg 连接 PostgreSQL 需要。
#   * curl：容器 HEALTHCHECK 用。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        zlib1g \
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
COPY --chown=app:app templates ./templates
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app deploy ./deploy
COPY --chown=app:app requirements.txt ./

# Django 应用逐个复制。
#
# 这里刻意**逐个列全**而不是 `COPY . .`：
#   逐个列可避免把 tests / .venv / 日志等打进来（.dockerignore 只是第二道防线）。
# 但代价是每新增一个应用都必须同步本清单 ——
# P2–P4 新增的 competitions / tasks / submissions / reviews / reports
# 此前就漏在这里，容器构建后会在启动时因 ImportError 直接崩溃
# （本地 runserver 测不出来，因为源码就在磁盘上）。
# verify_deploy.py 里有一条检查专门比对「INSTALLED_APPS 中的应用目录
# 是否都在本文件里被 COPY」，防止再次漏掉。
COPY --chown=app:app accounts ./accounts
COPY --chown=app:app competitions ./competitions
COPY --chown=app:app core ./core
COPY --chown=app:app tasks ./tasks
COPY --chown=app:app submissions ./submissions
COPY --chown=app:app reviews ./reviews
COPY --chown=app:app reports ./reports

# 静态资源目录、日志目录与私有数据目录（collectstatic 与运行期写入需要）
# /data 是 P5 的私有附件挂载点：生产由 docker-compose bind mount 到宿主机
# 持久化目录；此处先建好并授权，使「未挂载时」的本地联调也能正常写入。
RUN mkdir -p /app/staticfiles /app/media /app/logs /data/private_media \
    && chown -R app:app /app /data

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
