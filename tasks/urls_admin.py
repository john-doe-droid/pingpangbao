"""tasks 管理端路由（命名空间：tasks_admin）。

挂载于 /manage/tasks/，仅供社团管理员使用：
维护工作类型、任务模板，在比赛下创建任务并分配人员。
"""
from django.urls import path

from . import views

app_name = "tasks_admin"

urlpatterns = [
    # --- 工作类型维护 ---
    path("work-types/", views.worktype_list, name="worktype_list"),
    path("work-types/new/", views.worktype_create, name="worktype_create"),
    path("work-types/<int:pk>/edit/", views.worktype_update, name="worktype_update"),
    path("work-types/<int:pk>/toggle/", views.worktype_toggle, name="worktype_toggle"),

    # --- 任务模板维护 ---
    path("templates/", views.template_list, name="template_list"),
    path("templates/new/", views.template_create, name="template_create"),
    path("templates/<int:pk>/edit/", views.template_update, name="template_update"),
    path("templates/<int:pk>/toggle/", views.template_toggle, name="template_toggle"),

    # --- 比赛任务与人员分配 ---
    path(
        "competitions/<int:competition_pk>/tasks/new/",
        views.task_create, name="task_create",
    ),
    path("tasks/<int:pk>/", views.task_detail, name="task_detail"),
    path("tasks/<int:pk>/edit/", views.task_update, name="task_update"),
    path("tasks/<int:pk>/assign/", views.task_assign, name="task_assign"),
    path(
        "assignments/<int:pk>/deactivate/",
        views.assignment_deactivate, name="assignment_deactivate",
    ),
]
