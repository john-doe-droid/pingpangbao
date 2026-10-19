"""submissions 应用配置。"""
from django.apps import AppConfig


class SubmissionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "submissions"
    verbose_name = "工作成果提交"
