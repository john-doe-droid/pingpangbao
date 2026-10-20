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
    # P2 新增业务模块
    "competitions",
    "tasks",
    # P3 新增业务模块
    "submissions",
    "reviews",
    # P4 新增：统计与报表
    "reports",
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

# ---------------------------------------------------------------------------
# P3：上传与配额上限（集中配置，视图里不再写死数字）
# 生产可用环境变量覆盖，见 .env.example
# ---------------------------------------------------------------------------
UPLOAD_LIMITS = {
    # 单文件输入上限（作用于用户上传的原始文件）
    "image_max_bytes": int(env("UPLOAD_IMAGE_MAX_BYTES", str(10 * 1024 * 1024))),
    "file_max_bytes": int(env("UPLOAD_FILE_MAX_BYTES", str(50 * 1024 * 1024))),
    "video_max_bytes": int(env("UPLOAD_VIDEO_MAX_BYTES", str(300 * 1024 * 1024))),
    # 个人图片配额（每用户全局累计）
    "images_per_user": int(env("UPLOAD_IMAGES_PER_USER", "50")),
    "image_bytes_per_user": int(
        env("UPLOAD_IMAGE_BYTES_PER_USER", str(100 * 1024 * 1024))
    ),
    # 图片处理参数
    "image_max_edge": int(env("UPLOAD_IMAGE_MAX_EDGE", "1280")),
    "image_quality": int(env("UPLOAD_IMAGE_QUALITY", "82")),
    "thumb_edge": int(env("UPLOAD_THUMB_EDGE", "400")),
    "thumb_quality": int(env("UPLOAD_THUMB_QUALITY", "78")),
    # 预签名下载 URL 有效期（秒）
    "presign_expires_seconds": int(env("COS_PRESIGN_EXPIRES", "300")),
}

# 私有附件目录：**不在** STATIC_ROOT / MEDIA_ROOT 之下，
# 因此不会被 Nginx 或 WhiteNoise 直接服务，必须经过鉴权视图下发。
#
# P5：区分「显式配置」与「开发默认值」。
# 开发环境沿用 BASE_DIR/private_media 省事；但生产环境若仍是这个默认值，
# 说明部署时漏配了数据目录 —— 附件会写进项目目录（容器内即临时层），
# 容器一重建就丢。build_storage() 会据此 fail-closed 报错。
_PRIVATE_MEDIA_ROOT_ENV = env("PRIVATE_MEDIA_ROOT", "").strip()
PRIVATE_MEDIA_ROOT = (
    Path(_PRIVATE_MEDIA_ROOT_ENV) if _PRIVATE_MEDIA_ROOT_ENV
    else Path(BASE_DIR / "private_media")
)
PRIVATE_MEDIA_ROOT_IS_EXPLICIT = bool(_PRIVATE_MEDIA_ROOT_ENV)

# 私有存储后端：'local'（服务器本地磁盘，P5 起为生产默认）或 'cos'。
# 同样区分显式配置与默认值：生产环境必须显式声明，否则 build_storage() 报错，
# 避免"忘了配"被静默当成 local 而落到容器临时层。
_PRIVATE_STORAGE_BACKEND_ENV = env("PRIVATE_STORAGE_BACKEND", "").strip().lower()
PRIVATE_STORAGE_BACKEND = _PRIVATE_STORAGE_BACKEND_ENV or "local"
PRIVATE_STORAGE_BACKEND_IS_EXPLICIT = bool(_PRIVATE_STORAGE_BACKEND_ENV)

# 腾讯云 COS 配置（仅在 PRIVATE_STORAGE_BACKEND=cos 时使用）
COS_CONFIG = {
    "region": env("COS_REGION", ""),
    "bucket": env("COS_BUCKET", ""),
    "secret_id": env("COS_SECRET_ID", ""),
    "secret_key": env("COS_SECRET_KEY", ""),
    "scheme": env("COS_SCHEME", "https"),
}
