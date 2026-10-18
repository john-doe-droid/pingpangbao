"""
测试公共 fixture。

要点：
- pytest-django 会自动为每个测试创建并销毁测试数据库（test_*），
  因此测试跑在真实 PostgreSQL 上，不是 SQLite。
- 测试里**不写任何真实密码或生产密钥**，一律使用临时值。
"""
import pytest
from django.contrib.auth import get_user_model

User = get_user_model()

# 仅供测试使用的弱口令（明确不用于生产；长度满足校验器要求）
TEST_PASSWORD = "TestPassw0rd!2026"
TEST_NEW_PASSWORD = "NewPassw0rd!2026"


@pytest.fixture
def plain_password():
    return TEST_PASSWORD


@pytest.fixture
def new_password():
    return TEST_NEW_PASSWORD


@pytest.fixture
def make_user(db):
    """创建用户；默认要求首登改密（与管理员创建账号的默认行为一致）。"""

    def _make(student_id="2025317020104", password=TEST_PASSWORD, **extra):
        extra.setdefault("real_name", "测试同学")
        extra.setdefault("must_change_password", True)
        return User.objects.create_user(student_id=student_id, password=password, **extra)

    return _make


@pytest.fixture
def active_user(make_user):
    """已通过首登改密、可正常使用的用户。"""
    user = make_user(student_id="2025317020199")
    user.must_change_password = False
    user.save(update_fields=["must_change_password"])
    return user
