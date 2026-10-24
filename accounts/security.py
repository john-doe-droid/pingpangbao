"""登录失败限流。"""
from __future__ import annotations

import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import LoginThrottle


SESSION_SECURITY_VERSION_KEY = "_pp_security_version"


def session_security_version_matches(request, user):
    """当前浏览器会话是否仍对应该账号的最新凭据。"""
    return request.session.get(SESSION_SECURITY_VERSION_KEY) == user.security_version


def sync_session_security_version(request, user):
    """登录或本人改密后，把当前会话标记为最新版本。"""
    request.session[SESSION_SECURITY_VERSION_KEY] = user.security_version


def login_throttle_key(account, ip):
    raw = "%s|%s" % ((account or "").strip().lower(), ip or "unknown")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_login_blocked(account, ip, *, now=None):
    now = now or timezone.now()
    row = LoginThrottle.objects.filter(key=login_throttle_key(account, ip)).first()
    if row is None or row.locked_until is None:
        return False
    if row.locked_until > now:
        return True
    row.delete()
    return False


def record_login_failure(account, ip, *, now=None):
    now = now or timezone.now()
    key = login_throttle_key(account, ip)
    with transaction.atomic():
        LoginThrottle.objects.get_or_create(
            key=key,
            defaults={"window_started_at": now},
        )
        row = LoginThrottle.objects.select_for_update().get(key=key)
        window_seconds = int(settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS)
        if row.window_started_at + timedelta(seconds=window_seconds) <= now:
            row.failure_count = 0
            row.window_started_at = now
            row.locked_until = None
        row.failure_count += 1
        if row.failure_count >= int(settings.LOGIN_RATE_LIMIT_ATTEMPTS):
            row.locked_until = now + timedelta(
                seconds=int(settings.LOGIN_RATE_LIMIT_LOCK_SECONDS)
            )
        row.save(update_fields=[
            "failure_count", "window_started_at", "locked_until", "updated_at",
        ])
        return row


def clear_login_failures(account, ip):
    LoginThrottle.objects.filter(
        key=login_throttle_key(account, ip),
    ).delete()
