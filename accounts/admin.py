"""accounts 后台注册：管理员在这里创建与维护账号。"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import Role, User


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "sort_order", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    ordering = ("sort_order", "code")

    # 业务代码只识别 club_admin 和 member；禁止在后台创建无效的新角色。
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """管理员创建账号的界面。

    ⚠️ 这里**不设置任何默认密码**。管理员新增用户时，
    在表单里填写初始密码；用户首次登录会被强制修改。
    """

    list_display = (
        "account", "real_name", "role_list",
        "is_active", "must_change_password", "last_login",
    )
    list_filter = ("is_active", "must_change_password", "is_staff", "roles")
    search_fields = ("account", "real_name", "phone")
    ordering = ("account",)
    filter_horizontal = ("roles", "groups", "user_permissions")

    fieldsets = (
        (None, {"fields": ("account", "password")}),
        (_("个人信息"), {"fields": ("real_name", "phone", "email")}),
        (_("角色与权限"), {
            "fields": ("roles", "is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
        }),
        (_("密码状态"), {"fields": ("must_change_password", "password_changed_at", "last_login_ip")}),
        (_("重要日期"), {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("account", "real_name", "phone", "roles",
                       "password1", "password2", "must_change_password"),
        }),
    )
    readonly_fields = ("password_changed_at", "last_login_ip", "last_login", "date_joined")

    @admin.display(description="角色")
    def role_list(self, obj):
        return "、".join(obj.roles.values_list("name", flat=True)) or "—"

    def save_model(self, request, obj, form, change):
        # 新建用户时，若管理员没显式关闭，则默认要求首登改密
        if not change:
            obj.must_change_password = True
        super().save_model(request, obj, form, change)
