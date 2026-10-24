"""
首登强制改密中间件。

行为：已登录用户若 must_change_password=True，则除白名单路径外，
一律重定向到改密页面。这样即使有人直接输入别的地址，也绕不过去。

白名单由 settings.PASSWORD_CHANGE_REQUIRED_EXEMPT_PREFIXES 提供，
包含改密页自身、退出登录、健康检查与静态资源。
"""
from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from .security import session_security_version_matches


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt_prefixes = tuple(
            getattr(settings, "PASSWORD_CHANGE_REQUIRED_EXEMPT_PREFIXES", [])
        )

    def __call__(self, request):
        user = getattr(request, "user", None)

        # 密码被重置、账号被停用或重新启用后，旧会话立即失效。
        # 升级后第一次访问也会要求重新登录，以建立版本标记。
        if (
            user is not None
            and user.is_authenticated
            and not session_security_version_matches(request, user)
        ):
            logout(request)
            if request.path.startswith("/health/"):
                return self.get_response(request)
            return redirect(reverse("accounts:login"))

        if (
            user is not None
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
            and not request.path.startswith(self.exempt_prefixes)
        ):
            return redirect(reverse("accounts:password_change"))

        return self.get_response(request)
