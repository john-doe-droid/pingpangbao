"""认证相关表单。"""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.request_ip import get_client_ip

from .security import (
    clear_login_failures,
    is_login_blocked,
    record_login_failure,
)
from .models import Role, User


class StudentLoginForm(AuthenticationForm):
    """登录表单：字段名沿用 username（Django 认证流程约定），标签使用账号。"""

    username = forms.CharField(
        label="账号",
        max_length=32,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "请输入账号",
                "autocomplete": "username",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label="密码",
        max_length=128,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "请输入密码",
                "autocomplete": "current-password",
            }
        ),
    )

    error_messages = {
        "invalid_login": "账号或密码不正确，请重试。",
        "inactive": "该账号已被停用，请联系管理员。",
    }

    def clean(self):
        account = (self.data.get("username") or "").strip()
        ip = get_client_ip(self.request)
        if is_login_blocked(account, ip):
            raise ValidationError(
                "登录尝试次数过多，请稍后再试。",
                code="login_blocked",
            )
        try:
            cleaned = super().clean()
        except ValidationError:
            record_login_failure(account, ip)
            raise
        clear_login_failures(account, ip)
        return cleaned


class FirstTimePasswordChangeForm(SetPasswordForm):
    """首登/常规改密表单。

    在 Django 的 SetPasswordForm 基础上：
    - 统一 Bootstrap 样式；
    - 增加"新密码不能与当前密码相同"的校验。
    """

    new_password1 = forms.CharField(
        label="新密码",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "autocomplete": "new-password",
                "placeholder": "至少 10 位，建议混合字母数字",
            }
        ),
        help_text="至少 10 个字符，不能全为数字，不能与账号过于相似。",
    )
    new_password2 = forms.CharField(
        label="确认新密码",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "autocomplete": "new-password",
                "placeholder": "请再次输入新密码",
            }
        ),
    )

    def clean_new_password1(self):
        new_password = self.cleaned_data.get("new_password1")
        if new_password and self.user.check_password(new_password):
            raise forms.ValidationError("新密码不能与当前密码相同。")
        return new_password


class ProfileForm(forms.Form):
    """本人可维护的展示资料。登录账号与正式姓名不在此处修改。"""

    display_name = forms.CharField(
        label="显示名称",
        required=False,
        max_length=50,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "例如：张晨",
            "autocomplete": "nickname",
        }),
        help_text="留空则显示姓名。该名称不会改变登录账号。",
    )
    current_password = forms.CharField(
        label="当前密码",
        strip=False,
        max_length=128,
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "autocomplete": "current-password",
        }),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_display_name(self):
        return (self.cleaned_data.get("display_name") or "").strip()

    def clean_current_password(self):
        password = self.cleaned_data.get("current_password")
        if not self.user or not self.user.check_password(password):
            raise forms.ValidationError("当前密码不正确。")
        return password


class ManagedMemberCreateForm(forms.ModelForm):
    """社团管理员创建普通成员账号。"""

    password1 = forms.CharField(
        label="初始密码",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    password2 = forms.CharField(
        label="确认初始密码",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = User
        fields = ["account", "real_name", "phone", "email"]
        widgets = {
            "account": forms.TextInput(attrs={"class": "form-control"}),
            "real_name": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
        }

    def clean_account(self):
        account = (self.cleaned_data.get("account") or "").strip()
        if not account:
            raise forms.ValidationError("账号不能为空。")
        return account

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "两次输入的密码不一致。")
        elif password1:
            candidate = User(account=cleaned.get("account", ""))
            try:
                validate_password(password1, candidate)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        user.must_change_password = True
        if commit:
            user.save()
            role = Role.objects.filter(code="member").first()
            if role:
                user.roles.add(role)
        return user


class ManagedMemberPasswordForm(forms.Form):
    """管理员为受管成员设置一次性密码。"""

    password1 = forms.CharField(
        label="新的初始密码",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    password2 = forms.CharField(
        label="确认新的初始密码",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "两次输入的密码不一致。")
        elif password1:
            if self.user and self.user.check_password(password1):
                self.add_error("password1", "新密码不能与当前密码相同。")
            try:
                validate_password(password1, self.user)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def save(self):
        self.user.set_password(self.cleaned_data["password1"])
        self.user.must_change_password = True
        self.user.password_changed_at = timezone.now()
        self.user.save(update_fields=[
            "password", "must_change_password", "password_changed_at",
        ])
        return self.user
