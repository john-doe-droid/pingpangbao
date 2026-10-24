"""
P2 测试专用 fixture。

放在 tests/p2/ 子目录里，因此：
- 父目录 tests/conftest.py 的 fixture（plain_password / make_user / active_user）
  依然可用，无需重复定义；
- **不需要修改 P1 的任何测试文件** —— P1 的 conftest.py 保持原样，
  回归对比才有意义。

注意 must_change_password：P1 的中间件会强制首登改密，
所有页面级测试用的用户都必须先清掉这个标记，否则会被重定向到改密页。
"""
import pytest
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Role
from competitions.models import Competition, CompetitionStatus
from tasks.models import CompetitionTask, TaskAssignment, TaskTemplate, WorkType

User = get_user_model()

P2_PASSWORD = "P2TestPassw0rd!2026"


# ---------------------------------------------------------------------------
# 角色（与 accounts/migrations/0002_seed_p2_roles.py 保持一致）
# ---------------------------------------------------------------------------

@pytest.fixture
def role_member(db):
    role, _ = Role.objects.get_or_create(
        code="member", defaults={"name": "干事", "sort_order": 10})
    return role


@pytest.fixture
def role_admin(db):
    role, _ = Role.objects.get_or_create(
        code="club_admin", defaults={"name": "社团管理员", "sort_order": 20})
    return role


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------

def _make_p2_user(account, real_name, password=P2_PASSWORD, **extra):
    """创建可直接使用的账号：不要求首登改密。

    注意 real_name 必须真的传进 create_user —— 它是模型必填字段，
    漏传会得到空姓名，进而让"页面上应/不应出现某人"这类断言失真。
    """
    extra.setdefault("must_change_password", False)
    user = User.objects.create_user(
        account=account, password=password, real_name=real_name, **extra)
    user.must_change_password = False
    user.save(update_fields=["must_change_password"])
    return user


@pytest.fixture
def p2_password():
    return P2_PASSWORD


@pytest.fixture
def admin_user(db, role_admin):
    """社团管理员：有 club_admin 角色，但不是 superuser。

    刻意不用 superuser —— 这样能真正检验"角色"是否生效，
    而不是被 is_superuser 的短路逻辑掩盖。
    """
    user = _make_p2_user("A0001", "管理员小张")
    user.roles.add(role_admin)
    return user


@pytest.fixture
def superuser(db):
    return _make_p2_user("A9999", "超级管理员", is_staff=True, is_superuser=True)


@pytest.fixture
def member_user(db, role_member):
    """普通干事。"""
    user = _make_p2_user("M0001", "干事小李")
    user.roles.add(role_member)
    return user


@pytest.fixture
def other_member(db, role_member):
    """另一名干事：用于验证"看不到别人的任务"。"""
    user = _make_p2_user("M0002", "干事小王")
    user.roles.add(role_member)
    return user


@pytest.fixture
def regular_user(db):
    """普通已登录、无管理角色的用户。"""
    return _make_p2_user("V0001", "普通用户小赵")


# ---------------------------------------------------------------------------
# 业务对象工厂
# ---------------------------------------------------------------------------

@pytest.fixture
def make_competition(db, admin_user):
    def _make(name="测试比赛", status=CompetitionStatus.DRAFT,
              event_date=None, deadline_delta_days=7, **extra):
        event_date = event_date or timezone.localdate()
        deadline = extra.pop(
            "default_deadline",
            timezone.now() + timedelta(days=deadline_delta_days),
        )
        # 归档状态必须在同一次 INSERT 里带上归档时间：
        # 数据库有 CheckConstraint(archived ⇒ archived_at 非空)，
        # 先建成 NULL 再补会直接违反约束。
        if status == CompetitionStatus.ARCHIVED:
            extra.setdefault("archived_at", timezone.now())
            extra.setdefault("archived_by", admin_user)
        return Competition.objects.create(
            name=name, event_date=event_date, default_deadline=deadline,
            status=status, created_by=admin_user, **extra,
        )
    return _make


@pytest.fixture
def active_competition(make_competition):
    return make_competition(name="进行中的比赛", status=CompetitionStatus.ACTIVE)


@pytest.fixture
def draft_competition(make_competition):
    return make_competition(name="草稿比赛", status=CompetitionStatus.DRAFT)


@pytest.fixture
def archived_competition(make_competition):
    return make_competition(name="已归档比赛", status=CompetitionStatus.ARCHIVED)


@pytest.fixture
def make_worktype(db, admin_user):
    def _make(name="宣传海报", is_active=True, **extra):
        return WorkType.objects.create(
            name=name, is_active=is_active, created_by=admin_user, **extra)
    return _make


@pytest.fixture
def worktype(db, make_worktype):
    return make_worktype()


@pytest.fixture
def make_template(db, admin_user, worktype):
    def _make(name="海报模板", work_type=None, **extra):
        return TaskTemplate.objects.create(
            name=name,
            work_type=work_type or extra.pop("work_type", None) or worktype,
            created_by=admin_user, **extra)
    return _make


@pytest.fixture
def template(db, make_template):
    return make_template()


@pytest.fixture
def make_task(db, admin_user, active_competition):
    """按 build_from_template 创建任务（保证快照正确）。"""
    def _make(template_obj, competition=None, **extra):
        competition = competition or active_competition
        task = CompetitionTask.build_from_template(
            competition=competition, template=template_obj,
            name=extra.pop("name", None) or template_obj.name,
            deadline=extra.pop("deadline", None),
            created_by=admin_user, **extra,
        )
        task.save()
        return task
    return _make


@pytest.fixture
def task(db, make_task, template):
    return make_task(template)


@pytest.fixture
def make_assignment(db, admin_user):
    def _make(task_obj, member, is_active=True):
        return TaskAssignment.objects.create(
            task=task_obj, member=member, assigned_by=admin_user, is_active=is_active)
    return _make


@pytest.fixture
def logged_in_client(client, p2_password):
    """返回一个可切换登录身份的辅助器。

    用法：
        logged_in_client.as_(member_user)
        resp = logged_in_client.get(url)
    """
    class Helper:
        def __init__(self, client):
            self.client = client

        def as_(self, user):
            # force_login 会绕过密码，但测试关注的是权限而非登录流程
            self.client.force_login(user)
            return self.client

        def logout(self):
            self.client.logout()
            return self.client

    return Helper(client)
