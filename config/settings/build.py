"""
构建期专用配置（仅用于镜像构建阶段的 collectstatic）。

为什么需要它：
    `collectstatic` 只需要 STATIC_ROOT 与静态文件查找器，**不需要**数据库、
          也不需要真实密钥。但如果直接沿用 prod.py，prod 会在导入时校验
    SECRET_KEY / ALLOWED_HOSTS / 数据库参数，而这些在构建阶段都不存在，
    会导致 `docker build` 直接失败。

    若为了绕过这一点而在构建时注入假密钥，那些值会被写进镜像层，属于
    把"假凭据留在产物里"，不可取。

因此：构建阶段用本配置（无敏感校验），运行阶段仍使用 prod.py。
本文件不包含任何真实密钥。
"""
from .base import *  # noqa: F401,F403

DEBUG = False

# 构建期不连接数据库：collectstatic 不读取 DATABASES，
# 这里留一个空引擎即可，避免任何真实连接尝试。
DATABASES = {}

# 构建期必须使用与生产**相同**的 manifest 存储后端。
#
# 为什么：prod.py 用 CompressedManifestStaticFilesStorage，模板里的
# `{% static %}` 会去读 staticfiles.json 的 manifest。如果构建期用普通存储
# 收集，产物里就没有 manifest，运行期访问任何带静态资源的页面都会
# 500（Missing staticfiles manifest entry）。
#
# manifest 存储只依赖 STATIC_ROOT 可写，不需要数据库，因此构建期可用。
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# 构建期忽略的密钥相关项（明确标注，避免误认为生产也这么宽松）
SECRET_KEY = "build-only-not-a-secret"  # noqa: S105  —— 仅用于构建期静态收集
ALLOWED_HOSTS = []
