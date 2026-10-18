"""core 路由：首页、健康检查与 favicon。"""
from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("health/live", views.live, name="health_live"),
    path("health/ready", views.ready, name="health_ready"),
    # 浏览器默认会请求 /favicon.ico；重定向到我们提供的 SVG 图标，
    # 避免日志里出现无意义的 404。
    path(
        "favicon.ico",
        RedirectView.as_view(url="/static/core/favicon.svg", permanent=True),
        name="favicon",
    ),
]
