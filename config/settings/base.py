"""
基础配置：开发与生产共用的部分。

本文件刻意不包含任何真实密钥。所有敏感值一律从环境变量读取，
缺失时的行为由各子配置（dev / prod）自行决定。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
# BASE_DIR 指向仓库根（包含 manage.py 的那一层）
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 读取仓库根下的 .env（若存在）。生产环境通常由部署平台注入环境变量，
# 此时 .env 不存在也不影响。
load_dotenv(BASE_DIR / ".env")


def env(name, default=None, required=False):
    """读取环境变量的统一入口。

    required=True 且缺失时抛错，绝不静默兜底 —— 生产配置缺失必须立刻可见。
    """
    value = os.environ.get(name, default)
    if required and (value is None or str(value).strip() == ""):
        raise RuntimeError(
            "缺少必需的环境变量 %s。\n"
            "请检查仓库根目录的 .env 文件（可参考 .env.example），"
            "或在部署环境的环境变量中补齐该项。" % name
        )
    return value


def env_bool(name, default=False):
    """把环境变量解析为布尔值；无法识别时报错而不是猜。"""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    v = str(raw).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise RuntimeError(
        "环境变量 %s 的取值无法识别为布尔值：%r（可用 true/false）" % (name, raw)
    )


def env_list(name, default=None):
    """把逗号分隔的环境变量解析为列表。"""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return list(default or [])
    return [item.strip() for item in str(raw).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# 应用
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

LOCAL_APPS = [
    "accounts",
    "core",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.ForcePasswordChangeMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# 认证 —— 必须在第一批迁移前指向自定义用户模型
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "accounts.backends.StudentIDBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:home"

# 密码强度：使用 Django 正规哈希器（PBKDF2 + 加盐），并保留升级空间
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# 国际化
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# 静态与媒体
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
# core/static 已被 APP_DIRS 的 app 静态目录机制收集，
# 因此不再重复登记到 STATICFILES_DIRS（否则 collectstatic 会报同名文件冲突）。
STATICFILES_DIRS = [BASE_DIR / "assets"] if (BASE_DIR / "assets").exists() else []
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# 会话与安全的基础值（具体强度由 prod 覆盖）
# ---------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False   # 前端表单需要读 CSRF token 的常规做法
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# 首登强制改密：中间件放行的路径前缀
PASSWORD_CHANGE_REQUIRED_EXEMPT_PREFIXES = [
    "/accounts/password/change/",
    "/accounts/logout/",
    "/health/",
    "/static/",
    "/media/",
    "/admin/logout/",
]

# 站点元信息（模板使用）
SITE_NAME = env("SITE_NAME", "中国农业大学乒乓球社工作统计")
