"""社团管理员的成员账号管理。"""
import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User


@pytest.fixture
def member_admin(db):
    role, _ = Role.objects.get_or_create(
        code="club_admin", defaults={"name": "社团管理员"},
    )
    user = User.objects.create_user(
        account="member-manager",
        password="ManagerPass!2026",
        real_name="成员管理员",
        must_change_password=False,
    )
    user.roles.add(role)
    return user


@pytest.mark.django_db
def test_member_manager_can_create_member(client, member_admin):
    client.force_login(member_admin)
    response = client.post(reverse("members_admin:create"), {
        "account": "new-member",
        "real_name": "新成员",
        "phone": "",
        "email": "",
        "password1": "MemberPass!2026",
        "password2": "MemberPass!2026",
    })
    assert response.status_code == 302
    member = User.objects.get(account="new-member")
    assert member.has_role("member") is True
    assert member.must_change_password is True


@pytest.mark.django_db
def test_member_manager_can_reset_and_toggle(client, member_admin):
    member_role, _ = Role.objects.get_or_create(
        code="member", defaults={"name": "干事"},
    )
    member = User.objects.create_user(
        account="managed-member",
        password="OldMemberPass!2026",
        real_name="受管成员",
        must_change_password=False,
    )
    member.roles.add(member_role)
    client.force_login(member_admin)

    reset = client.post(reverse("members_admin:password", args=[member.pk]), {
        "password1": "NewMemberPass!2026",
        "password2": "NewMemberPass!2026",
    })
    assert reset.status_code == 302
    member.refresh_from_db()
    assert member.check_password("NewMemberPass!2026") is True
    assert member.must_change_password is True

    toggle = client.post(reverse("members_admin:toggle", args=[member.pk]))
    assert toggle.status_code == 302
    member.refresh_from_db()
    assert member.is_active is False


@pytest.mark.django_db
def test_regular_member_cannot_manage_members(client, make_user):
    member_role, _ = Role.objects.get_or_create(
        code="member", defaults={"name": "干事"},
    )
    member = make_user(account="regular-member", must_change_password=False)
    member.roles.add(member_role)
    client.force_login(member)
    assert client.get(reverse("members_admin:list")).status_code == 403


@pytest.mark.django_db
def test_member_manager_cannot_reset_superuser(client, member_admin):
    superuser = User.objects.create_superuser(
        account="protected-admin",
        password="ProtectedPass!2026",
        real_name="超级管理员",
    )
    client.force_login(member_admin)
    response = client.get(reverse("members_admin:password", args=[superuser.pk]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_password_reset_revokes_existing_sessions(client, member_admin):
    """重置密码后，旧浏览器不能被继续使用。"""
    member_role, _ = Role.objects.get_or_create(
        code="member", defaults={"name": "干事"},
    )
    member = User.objects.create_user(
        account="session-member",
        password="OldSessionPass!2026",
        real_name="会话测试成员",
        must_change_password=False,
    )
    member.roles.add(member_role)

    old_client = Client()
    assert old_client.login(
        username=member.account, password="OldSessionPass!2026",
    )

    client.force_login(member_admin)
    response = client.post(reverse("members_admin:password", args=[member.pk]), {
        "password1": "NewSessionPass!2026",
        "password2": "NewSessionPass!2026",
    })
    assert response.status_code == 302

    response = old_client.get(reverse("tasks:my_tasks"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))
    assert "_auth_user_id" not in old_client.session


@pytest.mark.django_db
def test_disable_and_reenable_does_not_restore_old_session(client, member_admin):
    """停用会断开旧会话；重新启用也不能让停用前的会话复活。"""
    member_role, _ = Role.objects.get_or_create(
        code="member", defaults={"name": "干事"},
    )
    member = User.objects.create_user(
        account="disabled-member",
        password="DisabledPass!2026",
        real_name="停用测试成员",
        must_change_password=False,
    )
    member.roles.add(member_role)

    old_client = Client()
    assert old_client.login(
        username=member.account, password="DisabledPass!2026",
    )

    client.force_login(member_admin)
    assert client.post(
        reverse("members_admin:toggle", args=[member.pk]),
    ).status_code == 302
    assert old_client.get(reverse("tasks:my_tasks")).status_code == 302

    assert client.post(
        reverse("members_admin:toggle", args=[member.pk]),
    ).status_code == 302
    response = old_client.get(reverse("tasks:my_tasks"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))
    assert "_auth_user_id" not in old_client.session
