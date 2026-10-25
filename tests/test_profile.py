"""Self-service display-name tests."""

import pytest
from django.urls import reverse

from core.models import AuditLog


@pytest.mark.django_db
def test_profile_requires_login(client):
    response = client.get(reverse("accounts:profile"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


@pytest.mark.django_db
def test_user_can_change_display_name_with_current_password(
        client, make_user, plain_password):
    user = make_user(
        account="profile-user",
        password=plain_password,
        must_change_password=False,
    )
    client.force_login(user)

    response = client.post(reverse("accounts:profile"), {
        "display_name": "小刘",
        "current_password": plain_password,
    })

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.display_name == "小刘"
    assert user.get_short_name() == "小刘"
    assert user.account == "profile-user"
    assert AuditLog.objects.filter(action="profile.display_name").exists()


@pytest.mark.django_db
def test_profile_rejects_wrong_current_password(
        client, make_user, plain_password):
    user = make_user(
        account="profile-user",
        password=plain_password,
        must_change_password=False,
    )
    user.display_name = "原名称"
    user.save(update_fields=["display_name"])
    client.force_login(user)

    response = client.post(reverse("accounts:profile"), {
        "display_name": "新名称",
        "current_password": "wrong-password",
    })

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.display_name == "原名称"
    assert "当前密码不正确" in response.content.decode("utf-8")
