"""可信请求 IP 解析。"""
from __future__ import annotations

import ipaddress

from django.conf import settings


def _valid_ip(value):
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return None


def get_client_ip(request):
    """返回经过校验的客户端 IP。

    只有显式开启 TRUST_PROXY_HEADERS 时才读取代理头，且 Nginx 必须覆盖
    X-Real-IP / X-Forwarded-For，不能保留客户端自带的同名头。
    """
    if request is None:
        return None

    remote = _valid_ip(request.META.get("REMOTE_ADDR"))
    if not getattr(settings, "TRUST_PROXY_HEADERS", False):
        return remote

    real_ip = _valid_ip(request.META.get("HTTP_X_REAL_IP"))
    if real_ip:
        return real_ip
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        for item in forwarded.split(","):
            candidate = _valid_ip(item)
            if candidate:
                return candidate
    return remote
