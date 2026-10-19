# -*- coding: utf-8 -*-
"""P3 测试专用 fixture。

放在 tests/p3/ 下，因此父目录 tests/conftest.py 与 tests/p2/conftest.py 的
fixture 依然可用（P1 的 make_user、P2 的 admin_user/member_user 等），
**不需要修改任何既有测试文件** —— 这样 P1/P2 回归对比才有意义。
"""
import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from datetime import timedelta
from PIL import Image

from accounts.models import Role
from competitions.models import Competition, CompetitionStatus, ReviewerAssignment
from core.storage import reset_storage_cache
from tasks.models import (
    CompetitionTask,
    TaskAssignment,
    TaskTemplate,
    WorkType,
)

User = get_user_model()

P3_PASSWORD = "P3TestPassw0rd!9"


# ---------------------------------------------------------------------------
# 图片造数据工具
# ---------------------------------------------------------------------------

def make_image_bytes(fmt="JPEG", size=(1600, 1200), color=(30, 60, 200),
                     mode="RGB", exif_orientation=None):
    """生成一张真实可解码的图片字节。"""
    if mode == "RGBA":
        im = Image.new("RGBA", size, color + (128,) if len(color) == 3 else color)
    elif mode == "P":
        im = Image.new("P", size)
    else:
        im = Image.new("RGB", size, color)
    buf = io.BytesIO()
    kwargs = {}
    if exif_orientation is not None:
        exif = im.getexif()
        exif[274] = exif_orientation      # 274 = Orientation
        kwargs["exif"] = exif
    im.save(buf, fmt, **kwargs)
    return buf.getvalue()


def make_upload(name="photo.jpg", data=None, content_type="image/jpeg"):
    """构造一个 Django 上传对象。"""
    if data is None:
        data = make_image_bytes()
    return SimpleUploadedFile(name, data, content_type=content_type)


@pytest.fixture
def image_bytes():
    return make_image_bytes


@pytest.fixture
def upload_factory():
    return make_upload


# ---------------------------------------------------------------------------
# 存储：测试统一用本地私有存储（不消耗真实 COS 资源）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_private_storage(tmp_path, settings):
    """每个测试用独立的私有目录，并固定为本地后端。

    这样测试之间不会互相看到对方的文件，也不会碰真实的 COS。
    仍然会验证"权限校验 + 短时签名链接"这套机制本身。
    """
    settings.PRIVATE_STORAGE_BACKEND = "local"
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "private_media"
    (tmp_path / "private_media").mkdir(parents=True, exist_ok=True)
    reset_storage_cache()
    yield
    reset_storage_cache()


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------

def _mk(sid, name, role_code=None, password=P3_PASSWORD, **extra):
    extra.setdefault("must_change_password", False)
    user = User.objects.create_user(
        student_id=sid, password=password, real_name=name, **extra)
    user.must_change_password = False
    user.save(update_fields=["must_change_password"])
    if role_code:
        role, _ = Role.objects.get_or_create(code=role_code, defaults={"name": role_code})
        user.roles.add(role)
    return user


@pytest.fixture
def p3_password():
    return P3_PASSWORD


@pytest.fixture
def p3_admin(db):
    return _mk("P3A001", "P3管理员", "club_admin")


@pytest.fixture
def p3_member(db):
    return _mk("P3M001", "P3干事甲", "member")


@pytest.fixture
def p3_member2(db):
    return _mk("P3M002", "P3干事乙", "member")


@pytest.fixture
def p3_outsider(db):
    """与比赛无关的第三个干事：用于验证跨用户访问被拒。"""
    return _mk("P3M003", "P3无关干事", "member")


# ---------------------------------------------------------------------------
# 业务对象
# ---------------------------------------------------------------------------

@pytest.fixture
def p3_worktype(db, p3_admin):
    return WorkType.objects.create(name="P3测试海报", created_by=p3_admin)


@pytest.fixture
def p3_template(db, p3_admin, p3_worktype):
    """默认模板：文字必填 + 图片必填 + 每人 2 条记录。"""
    return TaskTemplate.objects.create(
        name="P3测试模板",
        work_type=p3_worktype,
        created_by=p3_admin,
        description="测试用任务",
        allow_text=True, require_text=True,
        allow_image=True, require_image=True,
        max_records_per_member=2,
    )


@pytest.fixture
def p3_competition(db, p3_admin):
    return Competition.objects.create(
        name="P3测试比赛",
        event_date=timezone.localdate(),
        default_deadline=timezone.now() + timedelta(days=3),
        status=CompetitionStatus.ACTIVE,
        created_by=p3_admin,
    )


@pytest.fixture
def p3_task(db, p3_admin, p3_competition, p3_template):
    task = CompetitionTask.build_from_template(
        competition=p3_competition, template=p3_template, created_by=p3_admin)
    task.save()
    return task


@pytest.fixture
def p3_assignment(db, p3_admin, p3_task, p3_member):
    return TaskAssignment.objects.create(
        task=p3_task, member=p3_member, assigned_by=p3_admin)


@pytest.fixture
def p3_assignment2(db, p3_admin, p3_task, p3_member2):
    return TaskAssignment.objects.create(
        task=p3_task, member=p3_member2, assigned_by=p3_admin)


@pytest.fixture
def p3_reviewer(db, p3_admin, p3_competition):
    """把管理员设为该比赛的授权审核人。"""
    ReviewerAssignment.objects.create(
        competition=p3_competition, reviewer=p3_admin, granted_by=p3_admin)
    return p3_admin


@pytest.fixture
def submitted(db, p3_assignment, p3_member, p3_task):
    """直接经编排层真实提交一条记录（走完整管线，含图片）。"""
    from submissions import orchestration

    class FakeFiles:
        def __init__(self, data):
            self._d = data

        def getlist(self, key):
            return self._d.get(key, [])

        def get(self, key):
            v = self._d.get(key)
            return v[0] if isinstance(v, list) and v else None

    return orchestration.submit_new(
        assignment_id=p3_assignment.pk,
        user=p3_member,
        text_content="测试提交内容",
        post_data={},
        files=FakeFiles({"images": [make_upload("t.jpg", make_image_bytes())]}),
    )


@pytest.fixture
def fake_files():
    """构造 request.FILES 替身。"""
    class FakeFiles:
        def __init__(self, data=None):
            self._d = data or {}

        def getlist(self, key):
            value = self._d.get(key, [])
            return value if isinstance(value, list) else [value]

        def get(self, key):
            v = self._d.get(key)
            if isinstance(v, list):
                return v[0] if v else None
            return v

    return FakeFiles


@pytest.fixture
def logged_in(client, p3_password):
    """切换登录身份的辅助器。"""
    class Helper:
        def as_(self, user):
            client.force_login(user)
            return client

        def out(self):
            client.logout()
            return client

    return Helper(client)
