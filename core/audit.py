"""
操作记录辅助函数。

统一从这里写审计记录，避免各视图各写一套、格式不一致。
"""
from .models import AuditLog
from .request_ip import get_client_ip


def _client_ip(request):
    return get_client_ip(request)


def log_action(request, action, target=None, detail=""):
    """写入一条操作记录。

    target 可以是任何有 pk 的对象；用其类名与 __str__ 作为可读描述。
    本函数**不应抛异常**：审计写入失败不应把用户的正常操作搞崩，
    但失败会记进服务端日志（由 Django 日志系统兜底）。
    """
    target_type = ""
    target_id = ""
    target_repr = ""
    if target is not None:
        target_type = target.__class__.__name__
        target_id = str(getattr(target, "pk", "") or "")
        try:
            target_repr = str(target)[:200]
        except Exception:  # noqa: BLE001 —— __str__ 出问题不应影响主流程
            target_repr = ""

    user = getattr(request, "user", None)
    actor = user if (user is not None and getattr(user, "is_authenticated", False)) else None

    return AuditLog.objects.create(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_repr=target_repr,
        detail=detail,
        ip_address=_client_ip(request),
    )


def log_action_for_user(user, action, target=None, detail="", request=None):
    """显式指定操作人的审计写入。

    服务层可能在没有 HTTP request 的场景下被调用（管理命令、测试、
    后台任务），此时用 `log_action(request, ...)` 会丢掉操作人。
    本函数让调用方直接给出 actor。

    `core.AuditLog` 才是唯一的审计表 —— 本函数不另建日志体系。
    """
    target_type = ""
    target_id = ""
    target_repr = ""
    if target is not None:
        target_type = target.__class__.__name__
        target_id = str(getattr(target, "pk", "") or "")
        try:
            target_repr = str(target)[:200]
        except Exception:  # noqa: BLE001
            target_repr = ""

    return AuditLog.objects.create(
        actor=user if (user is not None and getattr(user, "is_authenticated", False)) else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_repr=target_repr,
        detail=detail,
        ip_address=_client_ip(request),
    )
