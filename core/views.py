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
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

from core.permissions import admin_required, user_is_admin

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
    from django.http import FileResponse, Http404, HttpResponse
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
        # 本地后端：直接下发文件
        return FileResponse(
            open(local_path, "rb"),
            as_attachment=True,
            filename=download_name,
        )

    # 非本地后端（COS 等）：后端读取对象内容后转发。
    #
    # 说明：这里刻意不直接 302 到预签名 URL 的另一种做法是——**由后端代理下发**。
    # 两者都能用，选择代理下发的理由：
    #   · 下载链接本身已带签名与有效期（token 校验在上面完成），
    #     权限判定完全在后端完成，不把对象存储域名暴露给浏览器；
    #   · 行为在两个后端上完全一致，便于测试与排障。
    # 代价是文件要过一次应用进程 —— 对工作成果这类几 MB 的附件可以接受；
    # 若将来出现几百 MB 的下载需求，可改为「校验后 302 到预签名 URL」。
    try:
        content = store.read(key)
    except FileNotFoundError:
        raise Http404("附件不存在或已被清理。")
    except Exception as exc:  # noqa: BLE001
        # 不把存储层的内部错误暴露给用户；细节进日志
        logger.error("附件读取失败：%s（%s）", key, type(exc).__name__)
        raise Http404("附件暂时无法下载，请稍后重试。") from exc

    response = HttpResponse(
        content,
        content_type=attachment.detected_content_type or "application/octet-stream",
    )
    response["Content-Disposition"] = 'attachment; filename="%s"' % download_name
    response["Content-Length"] = str(len(content))
    return response


def home(request):
    """登录后的角色化首页：成员直达工作，管理员查看待处理概览。"""
    if not request.user.is_authenticated:
        return redirect("accounts:login")

    if not user_is_admin(request.user):
        return redirect("tasks:my_tasks")

    from django.db.models import Q
    from django.utils import timezone

    from competitions.models import Competition, CompetitionStatus
    from submissions.models import Submission, SubmissionStatus
    from tasks.models import CompetitionTask

    now = timezone.now()
    active_tasks = CompetitionTask.objects.filter(
        competition__status=CompetitionStatus.ACTIVE,
    )
    overdue_tasks = active_tasks.filter(
        Q(deadline__isnull=False, deadline__lt=now)
        | Q(deadline__isnull=True, competition__default_deadline__lt=now)
    ).count()
    unassigned_tasks = (
        active_tasks
        .exclude(assignments__is_active=True)
        .distinct()
        .count()
    )

    dashboard = {
        "pending_submissions": Submission.objects.filter(
            status=SubmissionStatus.PENDING,
        ).count(),
        "needs_revision": Submission.objects.filter(
            status=SubmissionStatus.NEEDS_REVISION,
        ).count(),
        "active_competitions": Competition.objects.filter(
            status=CompetitionStatus.ACTIVE,
        ).count(),
        "overdue_tasks": overdue_tasks,
        "unassigned_tasks": unassigned_tasks,
    }
    return render(request, "core/home.html", {"dashboard": dashboard})


@admin_required
def visit_dashboard(request):
    """Readable, privacy-minimized visitor analytics for club administrators."""
    from datetime import datetime, time, timedelta

    from django.db.models import Count
    from django.db.models.functions import TruncDate
    from django.utils import timezone

    from .models import VisitorEvent

    period = request.GET.get("period", "7")
    if period not in {"1", "7", "30"}:
        period = "7"
    days = int(period)
    now = timezone.now()
    first_day = timezone.localdate(now) - timedelta(days=days - 1)
    start = timezone.make_aware(
        datetime.combine(first_day, time.min),
        timezone.get_current_timezone(),
    )
    events = VisitorEvent.objects.filter(created_at__gte=start)
    page_events = events.filter(event_type=VisitorEvent.EventType.PAGE)

    unique_visitors = (
        page_events.exclude(visitor_hash="")
        .values("visitor_hash")
        .distinct()
        .count()
    )
    online_since = now - timedelta(minutes=15)
    online_visitors = (
        VisitorEvent.objects.filter(
            created_at__gte=online_since,
            event_type=VisitorEvent.EventType.PAGE,
        )
        .exclude(visitor_hash="")
        .values("visitor_hash")
        .distinct()
        .count()
    )

    daily_rows = list(
        page_events
        .annotate(day=TruncDate(
            "created_at", tzinfo=timezone.get_current_timezone(),
        ))
        .values("day")
        .annotate(views=Count("id"))
        .order_by("day")
    )
    daily_map = {row["day"]: row["views"] for row in daily_rows}
    daily = []
    for offset in range(days - 1, -1, -1):
        day = timezone.localdate(now) - timedelta(days=offset)
        daily.append({"day": day, "views": daily_map.get(day, 0)})
    peak = max((row["views"] for row in daily), default=0)
    for row in daily:
        row["height"] = max(4, round(row["views"] * 100 / peak)) if peak else 4

    recent = list(
        events.select_related("user").order_by("-created_at", "-id")[:80]
    )
    for event in recent:
        if event.user_id:
            event.visitor_label = event.user.get_short_name()
            event.visitor_account = event.user.account
        elif event.account_attempt:
            event.visitor_label = event.account_attempt
            event.visitor_account = "登录失败"
        else:
            event.visitor_label = "访客"
            event.visitor_account = ""

    return render(request, "core/visits.html", {
        "page_title": "访问统计",
        "period": period,
        "days": days,
        "total_views": page_events.count(),
        "unique_visitors": unique_visitors,
        "online_visitors": online_visitors,
        "logged_in_visitors": (
            page_events.filter(user__isnull=False)
            .values("user_id").distinct().count()
        ),
        "failed_logins": events.filter(
            event_type=VisitorEvent.EventType.LOGIN_FAILED,
        ).count(),
        "daily": daily,
        "top_pages": (
            page_events.values("path")
            .annotate(views=Count("id"))
            .order_by("-views", "path")[:8]
        ),
        "top_users": (
            page_events.filter(user__isnull=False)
            .values(
                "user__account", "user__display_name", "user__real_name",
            )
            .annotate(views=Count("id"))
            .order_by("-views", "user__account")[:8]
        ),
        "recent": recent,
    })
