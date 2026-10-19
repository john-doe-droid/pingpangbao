# -*- coding: utf-8 -*-
"""submissions 管理端路由（命名空间：submissions_admin）。

挂载于 /manage/submissions/，仅社团管理员可用。
"""
from django.urls import path

from . import views

app_name = "submissions_admin"

urlpatterns = [
    path("", views.manage_submissions, name="list"),
]
