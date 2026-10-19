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


def private_file(request, key):
    """私有附件下载（P3）。

    三重保护：
      1. 必须登录 —— 匿名一律 403（附件不是公开资源）；
      2. token 必须有效且**未过期** —— 由 core.storage.verify_download_token 校验；
      3. 必须对该附件有查看权限 —— 跨用户访问被拒。

    注意：本视图**不**位于 /static/ 或 /media/ 之下，因此不会被 Nginx
    或 WhiteNoise 直接服务；私有目录 PRIVATE_MEDIA_ROOT 也不在 STATIC_ROOT 内。
    """
    from django.http import FileResponse, Http404
    from django.core.exceptions import PermissionDenied

    from core.storage import get_storage, verify_download_token

    if not getattr(request.user, "is_authenticated", False):
        raise PermissionDenied("请先登录后再下载附件。")

    # token 校验：无效或被篡改 → 拒绝；已过期 → 拒绝
    token = request.GET.get("token", "")
    token_key = verify_download_token(token) if token else None
    if token_key is None:
        raise PermissionDenied("下载链接无效或已过期，请回到页面重新获取。")
    # token 绑定的对象必须与 URL 中的一致，防止用 A 的 token 取 B 的文件
    if token_key != key:
        raise PermissionDenied("下载链接与目标文件不匹配。")

    # 权限校验：先按对象键找到附件，再判断该用户能否查看
    from submissions.models import Attachment
    from submissions.permissions import can_view_attachment

    attachment = (
        Attachment.objects
        .select_related("version", "version__submission",
                        "version__submission__assignment",
                        "version__submission__assignment__task",
                        "version__submission__assignment__task__competition",
                        "version__submission__assignment__member")
        .filter(storage_key=key)
        .first()
    )
    if attachment is None:
        raise Http404("附件不存在。")
    if not can_view_attachment(request.user, attachment):
        raise PermissionDenied("您没有权限访问该附件。")

    store = get_storage()
    local_path = store.open_local_path(key)

    download_name = request.GET.get("name") or attachment.original_name or key
    # 去掉可能影响响应头的字符
    download_name = download_name.replace('"', "").replace("\r", "").replace("\n", "")

    if local_path is not None:
        return FileResponse(
            open(local_path, "rb"),
            as_attachment=True,
            filename=download_name,
        )

    # COS 等非本地后端在此场景应改用预签名直链；走到这里说明实现有遗漏
    logger.error("存储后端没有本地路径，无法直接下发：%s", key)
    raise Http404("该附件无法通过此方式下载，请重新获取下载链接。")


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
