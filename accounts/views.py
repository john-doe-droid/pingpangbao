"""
accounts 视图。

本阶段只做认证基础，**不提供公开自助注册**（管理员统一创建账号）。
"""
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from core.audit import log_action
from core.permissions import admin_required
from core.request_ip import get_client_ip

from .forms import (
    FirstTimePasswordChangeForm,
    ManagedMemberCreateForm,
    ManagedMemberPasswordForm,
    ProfileForm,
    StudentLoginForm,
)
from .models import User
from .security import sync_session_security_version


class StudentLoginView(LoginView):
    """登录。成功后按 must_change_password 决定去向。"""

    template_name = "accounts/login.html"
    authentication_form = StudentLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user

        # 记录登录 IP，供后续审计
        ip = get_client_ip(self.request)
        if ip:
            type(user).objects.filter(pk=user.pk).update(last_login_ip=ip)

        if getattr(user, "must_change_password", False):
            messages.warning(
                self.request,
                "这是您首次登录，请先修改初始密码后再继续使用。",
            )
            return redirect("accounts:password_change")

        return response

class StudentLogoutView(LogoutView):
    next_page = reverse_lazy("core:home")


@login_required
def profile(request):
    """Allow users to change their visible name, not their login identity."""
    if request.method == "POST":
        form = ProfileForm(request.POST, user=request.user)
        if form.is_valid():
            old_name = request.user.get_short_name()
            request.user.display_name = form.cleaned_data["display_name"]
            request.user.save(update_fields=["display_name"])
            new_name = request.user.get_short_name()
            log_action(
                request, "profile.display_name", request.user,
                detail="%s -> %s" % (old_name, new_name),
            )
            messages.success(request, "显示名称已更新。")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(
            user=request.user,
            initial={"display_name": request.user.display_name},
        )

    return render(request, "accounts/profile.html", {
        "form": form,
        "page_title": "个人资料",
    })


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
            sync_session_security_version(request, user)
            messages.success(request, "密码修改成功。")
            return redirect("core:home")
    else:
        form = FirstTimePasswordChangeForm(request.user)

    return render(
        request,
        "accounts/password_change.html",
        {"form": form, "force_mode": force_mode},
    )


def _managed_members_queryset():
    """仅返回普通成员，防止社团管理员通过此入口修改高权限账号。"""
    return (
        User.objects
        .filter(roles__code="member")
        .exclude(is_superuser=True)
        .exclude(is_staff=True)
        .exclude(roles__code="club_admin")
        .distinct()
        .order_by("account")
    )


@admin_required
def managed_members(request):
    return render(request, "accounts/managed_members.html", {
        "members": _managed_members_queryset(),
        "page_title": "成员管理",
    })


@admin_required
def managed_member_create(request):
    if request.method == "POST":
        form = ManagedMemberCreateForm(request.POST)
        if form.is_valid():
            member = form.save()
            log_action(request, "member.create", member)
            messages.success(
                request,
                "成员账号已创建。请把账号和初始密码告知本人，首次登录会要求改密。",
            )
            return redirect("members_admin:list")
        messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = ManagedMemberCreateForm()
    return render(request, "accounts/member_form.html", {
        "form": form,
        "page_title": "新建成员账号",
        "is_create": True,
    })


@admin_required
def managed_member_password(request, pk):
    member = get_object_or_404(_managed_members_queryset(), pk=pk)
    if request.method == "POST":
        form = ManagedMemberPasswordForm(request.POST, user=member)
        if form.is_valid():
            form.save()
            log_action(
                request, "member.password_reset", member,
                detail="重置初始密码并要求下次登录修改",
            )
            messages.success(request, "初始密码已重置，成员下次登录必须修改密码。")
            return redirect("members_admin:list")
        messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = ManagedMemberPasswordForm(user=member)
    return render(request, "accounts/member_form.html", {
        "form": form,
        "member": member,
        "page_title": "重置成员密码",
        "is_create": False,
    })


@admin_required
@require_POST
def managed_member_toggle(request, pk):
    member = get_object_or_404(_managed_members_queryset(), pk=pk)
    member.is_active = not member.is_active
    # 停用后立即断开已有会话；重新启用时也换一个新版本，
    # 防止停用前保留下来的旧会话在重新启用后“复活”。
    member.security_version += 1
    member.save(update_fields=["is_active", "security_version"])
    action = "member.enable" if member.is_active else "member.disable"
    log_action(request, action, member)
    messages.success(
        request,
        "账号已%s。" % ("启用" if member.is_active else "停用"),
    )
    return redirect("members_admin:list")
