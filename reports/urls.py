# -*- coding: utf-8 -*-
"""reports 路由。

路径：
    /reports/              报表页面（筛选 + 预览）
    /reports/export/       导出 XLSX（同一筛选条件）

刻意放在 /reports/ 而不是 /my/ 或 /manage/：报表对三种角色都可用，
只是**可见数据范围**不同（由权限层决定，不由路径决定）。
"""
from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.report_page, name="page"),
    path("export/", views.export_xlsx, name="export"),
]
