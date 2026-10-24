# -*- coding: utf-8 -*-
"""P4 测试专用 fixture。

复用 tests/p3/conftest.py 的全部 fixture（`submitted`、`p3_member`、
`p3_worktype`、`isolated_private_storage` 等），因此**不需要修改任何既有测试**。

本文件只补 P4 需要的东西：
    · 多版本数据（验证"版本不重复计入记录数"）
    · 跨工作类型/多成员的多条记录（验证汇总加总一致）
    · 已登录的测试 client
"""
import pytest
from django.contrib.auth import get_user_model

from accounts.models import Role
from competitions.models import ReviewerAssignment
from submissions import orchestration, services
from submissions.models import Submission
from tasks.models import CompetitionTask, TaskAssignment, TaskTemplate, WorkType

from tests.p3.conftest import make_image_bytes, make_upload

User = get_user_model()


# ---------------------------------------------------------------------------
# 从 P3 复用 fixture
#
# pytest 的 conftest 按目录生效：tests/p3/ 下定义的 fixture 在 tests/p4/ 不可见。
# 可选两种做法：
#   ① pytest_plugins = ["tests.p3.conftest"]
#      ❌ pytest 8 起禁止在**非顶层** conftest 使用它 —— 会让整个套件收集失败
#   ② 在本文件用同名 fixture 包装 P3 的实现
#      ✅ 采用这条：不改动 P3 任何文件，也不违反 pytest 规则
#
# 包装时必须显式写出依赖，让 pytest 能解析依赖链。
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_private_storage(tmp_path, settings):
    """与 P3 一致：每个测试用独立的私有附件目录 + 固定本地存储后端。

    P3 的这个 fixture 是 autouse，但在 tests/p4/ 不可见；
    P4 同样需要它（`submitted` / `rich_dataset` 会真实写附件文件），
    因此在这里重新定义一份等价实现。
    """
    from core.storage import reset_storage_cache

    settings.PRIVATE_STORAGE_BACKEND = "local"
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "private_media"
    (tmp_path / "private_media").mkdir(parents=True, exist_ok=True)
    reset_storage_cache()
    yield
    reset_storage_cache()


@pytest.fixture
def p3_admin(db):
    """P4 复用 P3 的管理员账号（同 P3 的 _mk 实现）。"""
    from tests.p3.conftest import _mk
    return _mk("P3A001", "P3管理员", "club_admin")


@pytest.fixture
def p3_member(db):
    from tests.p3.conftest import _mk
    return _mk("P3M001", "P3干事甲", "member")


@pytest.fixture
def p3_member2(db):
    from tests.p3.conftest import _mk
    return _mk("P3M002", "P3干事乙", "member")


@pytest.fixture
def p3_worktype(db, p3_admin):
    from tasks.models import WorkType
    return WorkType.objects.create(name="P3测试海报", created_by=p3_admin)

# 说明：P4 需要 P3 那套业务 fixture（p3_admin / p3_member / ...）。
# pytest 的 conftest 是**按目录生效**的，tests/p3/ 下的 fixture 在 tests/p4/
# 不可见。有两条路：
#   ① pytest_plugins = ["tests.p3.conftest"]
#      ❌ pytest 8 起禁止在**非顶层** conftest 使用 pytest_plugins
#         （会导致整个套件收集失败），因此不能用；
#   ② 在本文件重新定义同名 fixture，内部 import 复用 P3 的实现。
#      ✅ 采用这条：不改动 P3 任何文件，且不违反 pytest 规则。
# 下面用 `pytest_plugins` 之外的方式实现：直接 import 出工厂函数，
# 再包一层同名 fixture。


class FakeFiles:
    """与 request.FILES 语义一致的替身（getlist 与 get 都能取到）。"""

    def __init__(self, data=None):
        self._d = data or {}

    def getlist(self, key):
        v = self._d.get(key, [])
        return v if isinstance(v, list) else [v]

    def get(self, key):
        v = self._d.get(key)
        if isinstance(v, list):
            return v[0] if v else None
        return v


def files_with_images(*names):
    return FakeFiles({"images": [
        make_upload(n, make_image_bytes(size=(900, 700))) for n in names]})


@pytest.fixture
def fake_files():
    return FakeFiles


@pytest.fixture
def report_admin(db):
    """报表用的管理员（club_admin 角色，非 superuser）。"""
    user = User.objects.create_user(
        account="P4R-ADM", password="P4Report!Pass9", real_name="报表管理员")
    user.must_change_password = False
    user.save(update_fields=["must_change_password"])
    role, _ = Role.objects.get_or_create(code="club_admin",
                                         defaults={"name": "社团管理员"})
    user.roles.add(role)
    return user


@pytest.fixture
def report_member(db, p3_member):
    """报表测试里的干事（复用 P3 的 p3_member 以保证密码一致）。"""
    return p3_member


@pytest.fixture
def rich_dataset(p3_admin, p3_worktype, p3_member, p3_member2):
    """构造用于报表校验的数据集。

    设计意图（每条都对应一个要验证的口径）：
      · m1 在"海报"类型下有 1 条记录，被退回重提 2 次 → 3 个版本但只算 1 条
      · m1 在"推文"类型下有 1 条记录 → 1 个版本
      · m2 在"海报"类型下有 1 条记录 → 1 个版本
    期望：记录数 3、版本数 5。
    """
    from django.utils import timezone
    from datetime import timedelta

    from competitions.models import Competition, CompetitionStatus

    article_type = WorkType.objects.create(name="P4推文", created_by=p3_admin)

    poster_tpl = TaskTemplate.objects.create(
        name="P4海报模板", work_type=p3_worktype, created_by=p3_admin,
        allow_text=True, require_text=True,
        allow_image=True, require_image=True, max_records_per_member=3,
    )
    article_tpl = TaskTemplate.objects.create(
        name="P4推文模板", work_type=article_type, created_by=p3_admin,
        allow_text=True, require_text=True,
        allow_image=True, require_image=True, max_records_per_member=3,
    )

    competition = Competition.objects.create(
        name="P4报表比赛",
        event_date=timezone.localdate(),
        default_deadline=timezone.now() + timedelta(days=5),
        status=CompetitionStatus.ACTIVE,
        created_by=p3_admin,
    )
    poster_task = CompetitionTask.build_from_template(
        competition=competition, template=poster_tpl, created_by=p3_admin)
    poster_task.save()
    article_task = CompetitionTask.build_from_template(
        competition=competition, template=article_tpl, created_by=p3_admin)
    article_task.save()

    asg_m1_poster = TaskAssignment.objects.create(
        task=poster_task, member=p3_member, assigned_by=p3_admin)
    asg_m2_poster = TaskAssignment.objects.create(
        task=poster_task, member=p3_member2, assigned_by=p3_admin)
    asg_m1_article = TaskAssignment.objects.create(
        task=article_task, member=p3_member, assigned_by=p3_admin)

    # 注意：同一比赛 + 同一人只能有一条有效授权（唯一约束保证），
    # 因此这里只建一次（本文件第一版写了两次，触发了 IntegrityError）。
    ReviewerAssignment.objects.create(
        competition=competition, reviewer=p3_admin, granted_by=p3_admin)

    # m1 海报：提交一次，然后退回并重提两次 → 3 个版本
    s_poster = orchestration.submit_new(
        assignment_id=asg_m1_poster.pk, user=p3_member, text_content="海报初版",
        post_data={}, files=files_with_images("p1.jpg"))
    for index in (1, 2):
        with __import__("django.db", fromlist=["transaction"]).transaction.atomic():
            services.reject_submission(
                submission=services.lock_submission(s_poster.pk),
                reviewer=p3_admin, comment="第 %d 次退回" % index)
        orchestration.submit_revision(
            submission_id=s_poster.pk, user=p3_member,
            text_content="海报第 %d 次修改" % (index + 1), post_data={},
            files=files_with_images("p%d.jpg" % (index + 1)))

    # m1 推文：1 版
    s_article = orchestration.submit_new(
        assignment_id=asg_m1_article.pk, user=p3_member, text_content="推文初版",
        post_data={}, files=files_with_images("a1.jpg"))

    # m2 海报：1 版，且审核通过
    s_m2 = orchestration.submit_new(
        assignment_id=asg_m2_poster.pk, user=p3_member2, text_content="乙的海报",
        post_data={}, files=files_with_images("b1.jpg"))
    with __import__("django.db", fromlist=["transaction"]).transaction.atomic():
        services.approve_submission(
            submission=services.lock_submission(s_m2.pk),
            reviewer=p3_admin, comment="通过")

    return {
        "competition": competition,
        "poster_task": poster_task,
        "article_task": article_task,
        "poster_type": p3_worktype,
        "article_type": article_type,
        "m1_poster": s_poster,
        "m1_article": s_article,
        "m2_poster": s_m2,
        "members": (p3_member, p3_member2),
        "expected_records": 3,
        "expected_versions": 5,      # 海报 3 版 + 推文 1 版 + 乙海报 1 版
    }


@pytest.fixture
def report_client(client):
    """可切换登录身份的测试 client。

    用法：
        report_client.as_(user)     以该用户身份登录
        report_client.out()         登出
    """

    class Helper:
        def __init__(self, client):
            self.client = client

        def as_(self, user):
            self.client.force_login(user)
            return self.client

        def out(self):
            self.client.logout()
            return self.client

    return Helper(client)


@pytest.fixture(autouse=True)
def _allow_testserver(settings):
    """django.test.Client 用 testserver 作为 Host，默认可能被 ALLOWED_HOSTS 拦下。

    这是测试环境的需要，不影响生产配置（本 fixture 只改测试进程内的 settings）。
    """
    hosts = list(settings.ALLOWED_HOSTS or [])
    if "testserver" not in hosts:
        settings.ALLOWED_HOSTS = hosts + ["testserver"]
