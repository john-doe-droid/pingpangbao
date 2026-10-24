"""
本地开发配置。

特点：DEBUG 打开、允许 localhost、数据库默认指向本地 PostgreSQL 开发库。
即使在本文件里，也不写死任何真实密钥 —— SECRET_KEY 未设置时会生成一次性
随机值并给出提示，绝不落到代码或文件里。
"""
import secrets

from .base import *  # noqa: F401,F403

DEBUG = env_bool("DJANGO_DEBUG", True)

# 开发环境下若未提供 SECRET_KEY，生成一个仅本次进程有效的随机值。
# 这样既不用把密钥写进仓库，也不会让本地开发因缺变量而跑不起来。
# 生产配置（prod.py）不提供这种兜底，必须显式提供。
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or secrets.token_urlsafe(50)

ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    ["127.0.0.1", "localhost", "0.0.0.0", "[::1]"],
)

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])

# 本地开发用 PostgreSQL（本阶段不使用 SQLite 作为数据库）
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "pingpong_dev"),
        "USER": env("POSTGRES_USER", "pingpong"),
        "PASSWORD": env("POSTGRES_PASSWORD", ""),
        "HOST": env("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            "connect_timeout": 10,
        },
    }
}

# 静态文件：开发环境由 Django 直接服务，便于即时看到改动
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{levelname}] {asctime} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
    },
}
