"""认证相关信号。"""
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from core.models import VisitorEvent
from core.visitors import record_visitor_event

from .security import sync_session_security_version


@receiver(user_logged_in)
def record_session_security_version(sender, request, user, **kwargs):
    """记录本次登录对应的会话版本。"""
    if request is not None:
        sync_session_security_version(request, user)


@receiver(user_logged_in)
def track_login(sender, request, user, **kwargs):
    if request is not None:
        record_visitor_event(
            request, VisitorEvent.EventType.LOGIN,
            user=user, path="/accounts/login/",
        )


@receiver(user_logged_out)
def track_logout(sender, request, user, **kwargs):
    if request is not None:
        record_visitor_event(
            request, VisitorEvent.EventType.LOGOUT,
            user=user, path="/accounts/logout/",
        )


@receiver(user_login_failed)
def track_login_failure(sender, credentials, request, **kwargs):
    if request is not None:
        account = ""
        if isinstance(credentials, dict):
            account = credentials.get("username") or ""
        record_visitor_event(
            request, VisitorEvent.EventType.LOGIN_FAILED,
            path="/accounts/login/", account_attempt=str(account),
        )
