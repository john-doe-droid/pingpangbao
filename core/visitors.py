"""Privacy-conscious visitor activity helpers."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging

from django.conf import settings

from .models import VisitorEvent
from .request_ip import get_client_ip

logger = logging.getLogger(__name__)


def mask_ip(value):
    """Return a display-safe IP; never persist the full address."""
    if not value:
        return ""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return ""
    if address.version == 4:
        return ".".join(str(address).split(".")[:3] + ["*"])
    unpacked = address.exploded.split(":")
    return ":".join(unpacked[:4] + ["*"])


def hash_ip(value):
    """Return a stable HMAC used only for approximate unique-visitor counts."""
    if not value:
        return ""
    message = str(value).encode("utf-8")
    return hmac.new(
        str(settings.SECRET_KEY).encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def client_family(user_agent):
    value = str(user_agent or "")
    lowered = value.lower()
    if "micromessenger" in lowered:
        return "微信"
    if "edg/" in lowered:
        return "Edge"
    if "firefox/" in lowered:
        return "Firefox"
    if "chrome/" in lowered or "crios/" in lowered:
        return "Chrome"
    if "safari/" in lowered:
        return "Safari"
    if not value:
        return "未知"
    return "其他"


def record_visitor_event(request, event_type, *, user=None, path="",
                         account_attempt=""):
    """Store a minimized analytics event without affecting the request."""
    if not getattr(settings, "VISITOR_ANALYTICS_ENABLED", True):
        return None
    try:
        ip = get_client_ip(request)
        if user is None:
            candidate = getattr(request, "user", None)
            if candidate is not None and candidate.is_authenticated:
                user = candidate
        label = user.get_full_name() if user is not None else ""
        return VisitorEvent.objects.create(
            event_type=event_type,
            user=user,
            user_label=(label or "")[:80],
            account_attempt=(account_attempt or "").strip()[:32],
            path=(path or request.path or "")[:255],
            ip_masked=mask_ip(ip),
            visitor_hash=hash_ip(ip),
            client=client_family(request.META.get("HTTP_USER_AGENT", "")),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("访问统计写入失败：%s", type(exc).__name__)
        return None
