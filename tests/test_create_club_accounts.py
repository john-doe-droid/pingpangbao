"""Tests for the one-time club account bootstrap command."""

from django.core.management import call_command

from accounts.models import Role, User


def test_create_club_accounts_creates_roles_and_password_sheet(db, tmp_path):
    Role.objects.get_or_create(code="club_admin", defaults={"name": "社团管理员"})
    Role.objects.get_or_create(code="member", defaults={"name": "干事"})
    output = tmp_path / "accounts.csv"

    call_command(
        "create_club_accounts",
        admins=2,
        members=3,
        output=str(output),
    )

    assert User.objects.filter(account__startswith="admin").count() == 2
    assert User.objects.filter(account__startswith="member").count() == 3
    assert User.objects.get(account="admin01").roles.filter(code="club_admin").exists()
    assert User.objects.get(account="member01").roles.filter(code="member").exists()
    assert all(User.objects.values_list("must_change_password", flat=True))
    assert len(output.read_text(encoding="utf-8-sig").splitlines()) == 6


def test_create_club_accounts_is_idempotent(db, tmp_path):
    Role.objects.get_or_create(code="club_admin", defaults={"name": "社团管理员"})
    Role.objects.get_or_create(code="member", defaults={"name": "干事"})
    output = tmp_path / "accounts.csv"

    call_command("create_club_accounts", admins=1, members=1, output=str(output))
    call_command("create_club_accounts", admins=1, members=1, output=str(output))

    assert User.objects.filter(account="admin01").count() == 1
    assert User.objects.filter(account="member01").count() == 1
