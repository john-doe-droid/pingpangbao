"""ASGI 入口（当前阶段未使用异步，保留标准入口便于后续扩展）。"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_asgi_application()
