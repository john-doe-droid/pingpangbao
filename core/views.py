"""
core 视图：首页 + 健康检查。

/health/live   —— 只检查应用进程能否正常响应（不碰数据库）。
/health/ready  —— 检查应用与数据库是否都具备服务条件。

安全约定：健康检查响应体**不得**包含密钥、数据库密码、主机名、内部堆栈等细节。
失败时只给出稳定的错误码与一句可读说明，细节写进服务端日志。
"""
import logging

from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache

from core.permissions import user_is_admin

logger = logging.getLogger(__name__)


@never_cache
def live(request):
    """存活探针：进程在跑就返回 200。

    刻意不访问数据库 —— 数据库短暂不可用不应导致容器被反复重启。
    """
    return JsonResponse({"status": "ok", "check": "live"}, status=200)


@never_cache
def ready(request):
    """就绪探针：应用与数据库都可用才返回 200。

    数据库不可用时返回 503，并给出**不含敏感信息**的错误说明。
    完整异常写入服务端日志，不返回给调用方。
    """
    db_ok = False

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        db_ok = True
    except Exception as exc:  # noqa: BLE001 —— 任何连接异常都视为未就绪
        # 只记录异常类型名，避免把连接串/凭据写进日志
        logger.error("健康检查：数据库不可用（%s）", type(exc).__name__)

    if db_ok:
        return JsonResponse(
            {"status": "ok", "check": "ready", "database": "ok"},
            status=200,
        )

    return JsonResponse(
        {
            "status": "unavailable",
            "check": "ready",
            "database": "error",
            "detail": "数据库当前不可用，请稍后重试或联系管理员。",
        },
        status=503,
    )


def home(request):
    """基础首页。

    展示当前已可用的入口。本阶段（P2）已提供比赛与任务管理；
    工作成果提交与审核仍属后续阶段。
    """
    checks = [
        {"name": "工程底座", "desc": "config / accounts / core 分层，密钥与配置全部来自环境变量"},
        {"name": "用户与角色", "desc": "学号登录，多角色并存，首登强制改密"},
        {"name": "比赛管理", "desc": "创建、修改、归档；草稿不对干事开放"},
        {"name": "任务模板", "desc": "材料要求与提交份数可页面维护，创建任务时冻结快照"},
        {"name": "人员分配", "desc": "一项任务可分配给多名干事，历史记录保留"},
        {"name": "审核人授权", "desc": "逐场授权；管理员身份本身不自动获得审核权"},
        {"name": "我的任务", "desc": "干事只能看到分配给自己的任务"},
    ]

    if request.user.is_authenticated:
        entries = [
            {"label": "我的任务", "url": reverse("tasks:my_tasks"), "primary": True},
            {"label": "比赛管理", "url": reverse("competitions:list"), "primary": False},
        ]
        if user_is_admin(request.user):
            entries += [
                {"label": "任务模板", "url": reverse("tasks_admin:template_list"), "primary": False},
                {"label": "工作类型", "url": reverse("tasks_admin:worktype_list"), "primary": False},
            ]
    else:
        entries = [{"label": "登录", "url": reverse("accounts:login"), "primary": True}]

    return render(request, "core/home.html", {
        "checks": checks,
        "entries": entries,
    })
