"""tasks 干事端路由（命名空间：tasks）。

挂载于 /my/tasks/。

这里**只有**两个入口，刻意不放置任何管理类路由：
    ""                我的任务列表
    "mine/<task_pk>/" 我的任务详情（对象级校验，只能看分配给自己的）

干事的日常操作不会误入管理页面；即使手工改 URL，
后端也会因对象级权限检查而拒绝。
"""
from django.urls import path

from . import views

app_name = "tasks"

urlpatterns = [
    path("", views.my_tasks, name="my_tasks"),
    path("mine/<int:task_pk>/", views.my_task_detail, name="my_task_detail"),
]
