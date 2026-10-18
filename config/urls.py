"""
config 顶层路由。

本阶段只挂载三个部分：
    /            —— 基础首页（core）
    /accounts/   —— 登录 / 退出 / 改密
    /health/     —— 健康检查（core）
    /admin/      —— Django 管理后台（管理员创建账号的入口）

后续 P2 的业务模块（competitions / tasks / submissions / reviews / reports）
在此处按 `include("<app>.urls")` 追加即可，保持每个模块路由自治。
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("", include("core.urls")),
]
