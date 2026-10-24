"""用户模型与认证行为测试（对应验收 B）。"""
import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


# ---------------------------------------------------------------------------
# 用户模型
# ---------------------------------------------------------------------------

def test_user_model_is_custom():
    """AUTH_USER_MODEL 必须指向自定义模型，而不是 django.contrib.auth.User。"""
    from django.conf import settings
    from django.contrib.auth.models import User as DjangoDefaultUser

    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert User is not DjangoDefaultUser


def test_username_field_is_account():
    assert User.USERNAME_FIELD == "account"


def test_create_user_hashes_password(db, plain_password):
    """密码必须以哈希形式存储，绝不出现明文。"""
    user = User.objects.create_user(account="2025317020104", password=plain_password)

    assert user.password != plain_password
    assert user.password.startswith("pbkdf2_"), "应使用 Django 的 PBKDF2 哈希"
    assert user.check_password(plain_password) is True
    assert user.check_password("wrong-password") is False


def test_create_user_requires_account(db):
    with pytest.raises(ValueError):
        User.objects.create_user(account="", password="x")


def test_account_is_unique(db, make_user):
    make_user(account="2025317020104")
    with pytest.raises(Exception):
        User.objects.create_user(account="2025317020104", password="TestPassw0rd!2026")


# ---------------------------------------------------------------------------
# 多重身份（不用单一枚举锁死角色）
# ---------------------------------------------------------------------------

def test_user_can_hold_multiple_roles(db, make_user):
    from accounts.models import Role

    coach = Role.objects.create(code="coach", name="教练")
    player = Role.objects.create(code="player", name="队员")

    user = make_user()
    user.roles.add(coach, player)
    user.refresh_from_db()

    codes = user.role_codes()
    assert codes == {"coach", "player"}
    assert user.has_role("coach") is True
    assert user.has_role("player") is True
    assert user.has_role("treasurer") is False
    assert user.has_role("coach", "treasurer") is True, "任一命中即通过"


def test_superuser_passes_any_role_check(db, make_user):
    admin = make_user(account="admin001", is_staff=True, is_superuser=True)
    assert admin.has_role("anything") is True


def test_inactive_role_does_not_grant_permission(db, make_user):
    from accounts.models import Role

    retired = Role.objects.create(code="retired", name="已停用", is_active=False)
    user = make_user()
    user.roles.add(retired)
    assert user.has_role("retired") is False


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------

def test_authenticate_with_account(client, make_user, plain_password):
    make_user(account="2025317020104", password=plain_password)
    assert client.login(username="2025317020104", password=plain_password) is True


def test_authenticate_with_wrong_password_fails(client, make_user):
    make_user(account="2025317020104")
    assert client.login(username="2025317020104", password="definitely-wrong") is False


def test_inactive_user_cannot_login(client, make_user):
    make_user(account="2025317020104", is_active=False)
    assert client.login(username="2025317020104", password="TestPassw0rd!2026") is False


# ---------------------------------------------------------------------------
# 无公开注册入口
# ---------------------------------------------------------------------------

def test_no_public_registration_route():
    """反向解析 register 必须失败 —— 本系统不提供自助注册。"""
    from django.urls import NoReverseMatch, reverse

    for name in ("accounts:register", "accounts:signup"):
        with pytest.raises(NoReverseMatch):
            reverse(name)


def test_login_page_has_no_register_link(client):
    """登录页不得出现自助注册入口。

    注意：页面上可以有一句"不提供自助注册"的说明文案，
    因此这里检测的是**真实的注册链接/表单动作**，而不是"注册"二字。
    """
    import re

    resp = client.get("/accounts/login/")
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")

    # 不存在指向注册页的链接
    assert not re.search(r'href="[^"]*regist[^"]*"', body, re.IGNORECASE)
    assert not re.search(r'href="[^"]*signup[^"]*"', body, re.IGNORECASE)
    # 不存在 action 指向注册的提交表单
    assert not re.search(r'action="[^"]*regist[^"]*"', body, re.IGNORECASE)
    # 唯一的提交按钮是"登录"
    assert "type=\"submit\"" in body
