"""
角色与权限的公共判定。

设计前提（复用 P1 的 Role 模型，不新建认证体系）：
- 角色用 Role 表的 code 标识，不硬编码枚举；
- 一个用户可同时拥有多个角色（多对多），判定用"任一命中"；
- superuser 视为拥有一切管理权限（便于运维），但**不因此获得审核权**
  —— 审核权必须来自 ReviewerAssignment 的显式授权（见 competitions/permissions.py）。

本模块只管"角色层面"的判定；"对象层面"的判定（能不能管这场比赛）
由各 app 的 permissions.py 负责。
"""
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

# --- 角色标识常量 ---------------------------------------------------------
# 与 accounts/migrations 里的种子数据保持一致。
ROLE_ADMIN = "club_admin"      # 社团管理员：全部管理操作
ROLE_MEMBER = "member"         # 干事：只能看分配给自己的任务
ROLE_VIEWER = "viewer"         # 全局查看：可浏览比赛列表/详情，但不能管理

MANAGEMENT_ROLES = (ROLE_ADMIN,)


def user_is_admin(user):
    """是否拥有社团管理员权限（可执行全部管理操作）。"""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user.has_role(ROLE_ADMIN)


def user_can_view_all(user):
    """是否可浏览全部（非草稿）比赛。

    注意：这只给"查看"权限，**不包含**审核权与管理权。
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user.has_role(ROLE_ADMIN, ROLE_VIEWER)


def user_is_member(user):
    """是否是干事（拥有 member 角色）。

    干事是"可以被分配任务"的身份。管理员也可以同时是干事。
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return user.has_role(ROLE_MEMBER)


# --- 视图装饰器 -----------------------------------------------------------

def admin_required(view_func):
    """要求社团管理员身份。

    未登录 → 跳登录页；已登录但权限不足 → 403（不是静默跳转，
    避免"看起来能点但没反应"的困惑）。
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return redirect_to_login(request)
        if not user_is_admin(user):
            raise PermissionDenied("需要社团管理员权限。")
        return view_func(request, *args, **kwargs)

    return _wrapped


def login_required_view(view_func):
    """要求登录（不限定角色）。"""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return redirect_to_login(request)
        return view_func(request, *args, **kwargs)

    return _wrapped


def redirect_to_login(request):
    """跳转到登录页。

    注意：settings.LOGIN_URL 存的是 **URL 名称**（"accounts:login"），
    不是路径。直接把它拼进 redirect() 会被 Django 当成协议名，
    抛出 DisallowedRedirect（400）。必须先 reverse 成真实路径。
    """
    from urllib.parse import quote

    from django.conf import settings
    from django.shortcuts import redirect
    from django.urls import reverse

    login_url = reverse(settings.LOGIN_URL)
    return redirect("%s?next=%s" % (login_url, quote(request.get_full_path())))


class AdminRequiredMixin:
    """基于类的视图用：要求管理员身份。"""

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return redirect_to_login(request)
        if not user_is_admin(user):
            raise PermissionDenied("需要社团管理员权限。")
        return super().dispatch(request, *args, **kwargs)


class LoginRequiredMixin:
    """基于类的视图用：只要求登录。"""

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return redirect_to_login(request)
        return super().dispatch(request, *args, **kwargs)
