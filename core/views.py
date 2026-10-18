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
from django.views.decorators.cache import never_cache

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

    本阶段刻意不实现任何业务页面（比赛/任务/提交/审核均不在范围内），
    只验证模板渲染、静态资源加载和响应式布局可用。
    """
    checks = [
        {"name": "项目骨架", "desc": "config / accounts / core 分层已就位"},
        {"name": "自定义用户模型", "desc": "学号登录，角色可多重，首批迁移内建立"},
        {"name": "配置分层", "desc": "开发与生产配置分离，密钥全部来自环境变量"},
        {"name": "PostgreSQL", "desc": "开发与生产均使用 PostgreSQL"},
        {"name": "健康检查", "desc": "/health/live 与 /health/ready 已提供"},
        {"name": "自动化验证", "desc": "scripts/verify_stage1.py 为统一入口"},
    ]
    return render(request, "core/home.html", {"checks": checks})
