"""
accounts 视图。

本阶段只做认证基础，**不提供公开自助注册**（管理员统一创建账号）。
"""
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone

from .forms import FirstTimePasswordChangeForm, StudentLoginForm


class StudentLoginView(LoginView):
    """登录。成功后按 must_change_password 决定去向。"""

    template_name = "accounts/login.html"
    authentication_form = StudentLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user

        # 记录登录 IP，供后续审计
        ip = self._client_ip(self.request)
        if ip:
            type(user).objects.filter(pk=user.pk).update(last_login_ip=ip)

        if getattr(user, "must_change_password", False):
            messages.warning(
                self.request,
                "这是您首次登录，请先修改初始密码后再继续使用。",
            )
            return redirect("accounts:password_change")

        # 登录后弹一次使用说明（按身份给不同内容）。
        #
        # 用 session 标记而不是数据库字段：这是纯展示状态，
        # "关掉浏览器再登录"确实应该再看到一次；
        # 也不该为了它给用户表加字段、写迁移。
        self.request.session["show_welcome"] = True
        return response

    @staticmethod
    def _client_ip(request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")


class StudentLogoutView(LogoutView):
    next_page = reverse_lazy("core:home")


@login_required
def password_change(request):
    """改密。首登用户被中间件强制引导到这里。"""
    force_mode = getattr(request.user, "must_change_password", False)

    if request.method == "POST":
        form = FirstTimePasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            user.mark_password_changed()
            # 改密后保持登录状态，否则用户会被登出、体验突兀
            update_session_auth_hash(request, user)
            request.user.password_changed_at = timezone.now()
            messages.success(request, "密码修改成功。")
            return redirect("core:home")
    else:
        form = FirstTimePasswordChangeForm(request.user)

    return render(
        request,
        "accounts/password_change.html",
        {"form": form, "force_mode": force_mode},
    )
