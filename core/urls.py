"""core 路由：首页、健康检查与 favicon。"""
from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("health/live", views.live, name="health_live"),
    path("health/ready", views.ready, name="health_ready"),
    # P3：私有附件下载。刻意**不**放在 /static/ 或 /media/ 下，
    # 因此不会被 Nginx/WhiteNoise 静态服务绕过鉴权。
    path("files/<str:key>", views.private_file, name="private_file"),
    # 浏览器默认会请求 /favicon.ico；重定向到社团旗帜徽标，
    # 避免日志里出现无意义的 404。
    path(
        "favicon.ico",
        RedirectView.as_view(
            url="/static/core/images/favicon-32.png", permanent=True,
        ),
        name="favicon",
    ),
]
