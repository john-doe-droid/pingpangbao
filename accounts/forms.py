"""认证相关表单。"""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm

from .models import User


class StudentLoginForm(AuthenticationForm):
    """登录表单：字段名沿用 username（Django 认证流程约定），
    但语义是学号，界面标签写清楚。"""

    username = forms.CharField(
        label="学号",
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "请输入学号",
                "autocomplete": "username",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label="密码",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "请输入密码",
                "autocomplete": "current-password",
            }
        ),
    )

    error_messages = {
        "invalid_login": "学号或密码不正确，请重试。",
        "inactive": "该账号已被停用，请联系管理员。",
    }


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
        help_text="至少 10 个字符，不能全为数字，不能与学号过于相似。",
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
