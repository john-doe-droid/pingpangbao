"""competitions 应用配置。"""
from django.apps import AppConfig


class CompetitionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "competitions"
    verbose_name = "比赛管理"
