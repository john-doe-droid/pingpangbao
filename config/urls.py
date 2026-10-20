"""
config 顶层路由。

    /                     基础首页（core）
    /accounts/            登录 / 退出 / 改密
    /health/              健康检查
    /competitions/        比赛管理（P2）
    /tasks/               干事端「我的任务」（P2）
    /manage/tasks/        任务配置管理端（P2）
    /admin/               Django 管理后台（建账号、应急运维）

P3+ 的业务模块（submissions / reviews / reports）
在此处按 `include("<app>.urls")` 追加即可，保持每个模块路由自治。
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("competitions/", include("competitions.urls")),
    # 干事端：/my/tasks/...（查询层面只返回本人任务，无管理入口）
    path("my/tasks/", include("tasks.urls")),
    # 管理端：工作类型、模板、任务与分配
    path("manage/tasks/", include("tasks.urls_admin")),
    # P3 干事端 + 审核端：/my/submissions/...
    path("my/submissions/", include("submissions.urls")),
    # P3 管理端：/manage/submissions/...
    path("manage/submissions/", include("submissions.urls_admin")),
    # P4 统计与报表：/reports/ 与 /reports/export/
    path("reports/", include("reports.urls")),
    path("", include("core.urls")),
]
