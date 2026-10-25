"""Visitor analytics privacy, permissions and retention tests."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from core.models import VisitorEvent


@pytest.mark.django_db
@override_settings(VISITOR_ANALYTICS_ENABLED=True)
def test_page_view_is_linked_to_logged_in_user(client, make_user):
    user = make_user(account="visitor01", must_change_password=False)
    client.force_login(user)

    response = client.get(reverse("competitions:list"))

    assert response.status_code == 200
    event = VisitorEvent.objects.filter(
        event_type=VisitorEvent.EventType.PAGE,
        user=user,
    ).latest("id")
    assert event.path == reverse("competitions:list")
    assert event.ip_masked == "127.0.0.*"
    assert len(event.visitor_hash) == 64
    assert event.visitor_hash != "127.0.0.1"


@pytest.mark.django_db
@override_settings(VISITOR_ANALYTICS_ENABLED=True)
def test_health_check_is_not_tracked(client):
    client.get(reverse("core:health_live"))
    assert not VisitorEvent.objects.exists()


@pytest.mark.django_db
@override_settings(VISITOR_ANALYTICS_ENABLED=True)
def test_failed_login_records_attempt_but_not_password(client, make_user):
    make_user(account="known-user", must_change_password=False)
    client.post(reverse("accounts:login"), {
        "username": "known-user",
        "password": "wrong-password",
    })

    event = VisitorEvent.objects.get(
        event_type=VisitorEvent.EventType.LOGIN_FAILED,
    )
    assert event.account_attempt == "known-user"
    assert not hasattr(event, "password")


@pytest.mark.django_db
@override_settings(VISITOR_ANALYTICS_ENABLED=True)
def test_visit_dashboard_is_admin_only(client, make_user):
    member = make_user(account="visitor-member", must_change_password=False)
    member.roles.add(Role.objects.get(code="member"))
    client.force_login(member)
    assert client.get(reverse("analytics:visits")).status_code == 403

    admin = make_user(account="visitor-admin", must_change_password=False)
    admin.roles.add(Role.objects.get(code="club_admin"))
    client.force_login(admin)
    response = client.get(reverse("analytics:visits"))
    assert response.status_code == 200
    assert "访问统计" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_cleanup_removes_only_expired_events():
    old = VisitorEvent.objects.create(path="/old/")
    VisitorEvent.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=31),
    )
    current = VisitorEvent.objects.create(path="/current/")

    call_command("cleanup_visitor_events", days=30)

    assert not VisitorEvent.objects.filter(pk=old.pk).exists()
    assert VisitorEvent.objects.filter(pk=current.pk).exists()
