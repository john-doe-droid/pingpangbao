"""
PostgreSQL 连接与迁移测试（对应验收 D）。

这些测试在真实的 PostgreSQL 测试库上运行（pytest-django 自动创建 test_* 库），
不是 SQLite。如果本机没有可用的 PostgreSQL，这些测试会失败 —— 这是有意的：
不允许用 SQLite 冒充生产数据库。
"""
import pytest
from django.db import connection


@pytest.mark.postgres
def test_engine_is_postgresql(settings):
    engine = settings.DATABASES["default"]["ENGINE"]
    assert engine == "django.db.backends.postgresql", \
        "测试必须跑在 PostgreSQL 上，而不是 %s" % engine
    assert "sqlite" not in engine.lower()


@pytest.mark.postgres
def test_can_connect_and_query(db):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)


@pytest.mark.postgres
def test_server_version_is_postgres_18(db):
    """确认连接的是 PostgreSQL 18.x。"""
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version")
        version = cursor.fetchone()[0]
    major = int(str(version).split(".")[0])
    assert major == 18, "期望 PostgreSQL 18，实际为 %s" % version


@pytest.mark.postgres
def test_transaction_and_rollback(db):
    """验证真实事务行为（SQLite 与 PG 在此差异明显）。"""
    from django.db import transaction

    from accounts.models import Role

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            Role.objects.create(code="tmp_role", name="临时角色")
            raise RuntimeError("故意回滚")

    assert not Role.objects.filter(code="tmp_role").exists()


@pytest.mark.postgres
def test_migrations_are_applied(db):
    """accounts.0001_initial 必须已被应用。"""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM django_migrations WHERE app = %s ORDER BY name",
            ["accounts"],
        )
        names = [row[0] for row in cursor.fetchall()]
    assert "0001_initial" in names


@pytest.mark.postgres
def test_custom_user_table_exists(db):
    """自定义用户表必须真实落库，且包含自定义字段。"""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'accounts_user'
            """
        )
        columns = {row[0] for row in cursor.fetchall()}

    assert columns, "accounts_user 表不存在"
    assert "account" in columns
    assert "must_change_password" in columns
    assert "password_changed_at" in columns


@pytest.mark.postgres
def test_m2m_roles_table_exists(db):
    """多对多角色表存在 —— 支撑多重身份设计。"""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'accounts_user_roles'
            """
        )
        assert cursor.fetchone() is not None


@pytest.mark.postgres
def test_unique_constraint_on_account(db):
    """账号唯一约束必须在数据库层生效，而不只是应用层校验。"""
    from django.db import IntegrityError

    from accounts.models import User

    User.objects.create_user(account="2025317020111", password="TestPassw0rd!2026")
    with pytest.raises(IntegrityError):
        User.objects.create_user(account="2025317020111", password="TestPassw0rd!2026")
