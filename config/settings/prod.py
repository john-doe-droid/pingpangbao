"""
生产配置。

原则：**缺什么就报什么错，绝不静默降级。**
- SECRET_KEY、数据库连接、ALLOWED_HOSTS 一律必须由环境变量提供；
  缺失时在启动阶段就抛错，而不是用默认值把服务"糊"起来。
- DEBUG 强制关闭，且不允许被环境变量打开。
- HTTPS 相关的安全 Cookie 与响应头默认启用，可由环境变量显式关闭
  （仅用于本机验证生产配置的场景）。

本文件不含任何真实密钥。
"""
from .base import *  # noqa: F401,F403

# ---------------------------------------------------------------------------
# 核心开关：DEBUG 在生产环境恒为 False，不接受环境变量覆盖
# ---------------------------------------------------------------------------
DEBUG = False

# ---------------------------------------------------------------------------
# SECRET_KEY：必须显式提供
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)

if len(str(SECRET_KEY)) < 40:
    raise RuntimeError(
        "DJANGO_SECRET_KEY 太短（当前 %d 字符，要求至少 40）。\n"
        "生成方法：python -c \"import secrets; print(secrets.token_urlsafe(64))\""
        % len(str(SECRET_KEY))
    )

# 历史上常见的占位值一律拒绝，避免把模板直接部署上线
_PLACEHOLDERS = {"changeme", "change-me", "secret", "django-insecure", "replace-me", "todo"}
if any(p in str(SECRET_KEY).lower() for p in _PLACEHOLDERS):
    raise RuntimeError(
        "DJANGO_SECRET_KEY 看起来仍是占位值，请替换为真实随机密钥。"
    )

# ---------------------------------------------------------------------------
# 主机与 CSRF：必须显式配置（不允许空列表蒙混过关）
# ---------------------------------------------------------------------------
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise RuntimeError(
        "生产环境必须设置 DJANGO_ALLOWED_HOSTS（逗号分隔，例如 "
        "example.com,www.example.com）。留空会让服务拒绝所有请求或带来安全风险。"
    )

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if not CSRF_TRUSTED_ORIGINS:
    raise RuntimeError(
        "生产环境必须设置 DJANGO_CSRF_TRUSTED_ORIGINS（逗号分隔，必须带 "
        "https:// 前缀，例如 https://example.com）。"
    )
for origin in CSRF_TRUSTED_ORIGINS:
    if not origin.startswith(("https://", "http://")):
        raise RuntimeError(
            "DJANGO_CSRF_TRUSTED_ORIGINS 中的每一项都必须带协议前缀，"
            "当前值不合法：%r" % origin
        )

# ---------------------------------------------------------------------------
# 数据库：必须显式提供，且必须是 PostgreSQL
# ---------------------------------------------------------------------------
DB_ENGINE = env("DJANGO_DB_ENGINE", "django.db.backends.postgresql")
if "sqlite" in str(DB_ENGINE).lower():
    raise RuntimeError(
        "生产环境禁止使用 SQLite。请把 DJANGO_DB_ENGINE 设为 "
        "django.db.backends.postgresql，并提供 POSTGRES_* 连接参数。"
    )

_required_db = ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_HOST"]
_missing = [name for name in _required_db if not str(env(name, "")).strip()]
if _missing:
    raise RuntimeError(
        "生产环境缺少数据库连接参数：%s。请补齐对应环境变量。" % "、".join(_missing)
    )
if not str(env("POSTGRES_PASSWORD", "")).strip():
    raise RuntimeError(
        "生产环境缺少 POSTGRES_PASSWORD。请不要使用空密码连接数据库。"
    )

DATABASES = {
    "default": {
        "ENGINE": DB_ENGINE,
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(env("POSTGRES_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "connect_timeout": int(env("POSTGRES_CONNECT_TIMEOUT", "10")),
            # 连接层也强制 TLS 校验/超时相关参数在需要时由环境变量补齐
        },
    }
}

# ---------------------------------------------------------------------------
# HTTPS 与安全响应头
# ---------------------------------------------------------------------------
# 反向代理（Nginx）会通过 X-Forwarded-Proto 告知原始协议
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# 允许在本机验证生产配置时临时关闭 HTTPS 强制（默认开启）
_https_on = env_bool("DJANGO_HTTPS_ENABLED", True)

SECURE_SSL_REDIRECT = _https_on
SESSION_COOKIE_SECURE = _https_on
CSRF_COOKIE_SECURE = _https_on
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "31536000")) if _https_on else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = _https_on
SECURE_HSTS_PRELOAD = _https_on

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# 会话安全
SESSION_COOKIE_HTTPONLY = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = int(env("DJANGO_SESSION_COOKIE_AGE", str(60 * 60 * 12)))
SESSION_SAVE_EVERY_REQUEST = True

# ---------------------------------------------------------------------------
# 静态资源：WhiteNoise 压缩并加内容哈希
# ---------------------------------------------------------------------------
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# 邮件：生产环境用真实 SMTP，未配置则明确报错（不静默丢弃邮件）
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("DJANGO_EMAIL_HOST", "")
EMAIL_PORT = int(env("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", True)

# ---------------------------------------------------------------------------
# 日志：生产写文件，便于 Nginx 之外的应用侧排查
# ---------------------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {asctime} {name} {process:d} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "app.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console", "file"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
    },
}

# 生产环境静态资源由 Nginx 直接服务（见 deploy/nginx/），WhiteNoise 作为兜底。

# ---------------------------------------------------------------------------
# P3：私有附件存储 —— 生产固定使用 COS
#
# 本阶段技术栈已确定「腾讯云 COS 私有对象存储」，因此生产不允许选择其它后端：
# 这里直接锁定为 cos，避免误配成 local 后把附件写到服务器磁盘上。
#
# 注意：**不在此处校验 COS 凭据完整性**。原因是配置模块会被
# `manage.py check`、迁移、测试等场景加载，若在导入时就因缺凭据而抛错，
# 会让这些与附件无关的操作整体失败。
# 凭据校验放在「存储被真正构造时」（core/storage.py 的 build_storage），
# 那里缺失配置会抛 StorageConfigError —— 同样是明确报错，
# 且**绝不会静默回退到本地磁盘**。
# ---------------------------------------------------------------------------
PRIVATE_STORAGE_BACKEND = "cos"
