"""模板上下文：全站通用的站点信息。"""
from django.conf import settings


def site_context(request):
    return {
        "SITE_NAME": getattr(settings, "SITE_NAME", "乒乓球社工作统计"),
        "DEBUG": settings.DEBUG,
    }
