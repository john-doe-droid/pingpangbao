"""
操作记录辅助函数。

统一从这里写审计记录，避免各视图各写一套、格式不一致。
"""
from .models import AuditLog


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


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
