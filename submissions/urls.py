# -*- coding: utf-8 -*-
"""submissions 干事端 + 审核端路由（命名空间：submissions）。

挂载于 /my/submissions/。刻意与管理端分开：
干事的入口里不会混进管理页面，即使手工改 URL 也会被对象级权限拦下。
"""
from django.urls import path

from . import views

app_name = "submissions"

urlpatterns = [
    # --- 干事端 ---
    path("", views.my_submissions, name="my_submissions"),
    path("new/<int:task_pk>/", views.submit_new, name="submit_new"),
    path("<int:pk>/", views.submission_detail, name="detail"),
    path("<int:pk>/revise/", views.submit_revision, name="submit_revision"),

    # --- 附件下载（生成短时链接后跳转）---
    path("attachment/<int:pk>/download/", views.attachment_download,
         name="attachment_download"),

    # --- 审核端 ---
    path("review/", views.review_queue, name="review_queue"),
    path("review/<int:pk>/", views.review_submission, name="review_submission"),
    path("review/<int:pk>/detail/", views.review_detail, name="review_detail"),
    path("review/<int:pk>/undo-rejection/", views.undo_rejection,
         name="undo_rejection"),
    path("review/<int:pk>/reopen/", views.reopen_submission, name="reopen_submission"),
]
