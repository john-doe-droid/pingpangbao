# -*- coding: utf-8 -*-
"""P3 补充测试：文件上传、孤立对象清理命令、非本地存储下载。

补这些的原因（这些是 P3 交付后自查发现的缺口，不是环境阻塞）：
  1. 原先只测了 file「被拒绝」，**没有测成功路径** ——
     真实缺陷（如类型判断写错）会被漏掉；
  2. `cleanup_orphans` 管理命令是 README 承诺的能力，必须有测试；
  3. 附件下载在 **COS 后端**（无本地路径）下的分支原先没有覆盖 ——
     早期实现会在这里直接 404，属于真实功能缺陷，现已改为后端代理下发。
"""
import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from submissions.models import Attachment, OrphanObject
from submissions.pipeline import prepare_file
from submissions.services import SubmissionError

from .conftest import make_image_bytes, make_upload


def make_pdf() -> bytes:
    return b"%PDF-1.4\n" + b"1 0 obj\n<<>>\nendobj\n" + b"x" * 500


def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", "hello")
    return buf.getvalue()


# ===========================================================================
# 普通文件：成功路径
# ===========================================================================

@pytest.mark.django_db
class TestPrepareFile:

    def test_pdf_accepted(self, p3_member):
        upload = SimpleUploadedFile("report.pdf", make_pdf(),
                                    content_type="application/pdf")
        item = prepare_file(upload, user=p3_member)
        assert item.material_type == "file"
        assert item.size_bytes == len(make_pdf())
        assert item.detected_content_type == "application/pdf"
        assert item.storage_key.endswith(".pdf")
        # 对象键必须是服务端 uuid，不含原始文件名
        assert "report" not in item.storage_key

    def test_zip_accepted(self, p3_member):
        upload = SimpleUploadedFile("pack.zip", make_zip(),
                                    content_type="application/zip")
        item = prepare_file(upload, user=p3_member)
        assert item.detected_content_type == "application/zip"
        assert item.storage_key.endswith(".zip")

    def test_utf8_text_accepted(self, p3_member):
        upload = SimpleUploadedFile(
            "notes.txt", "训练记录：正手 50 次".encode("utf-8"),
            content_type="text/plain",
        )
        item = prepare_file(upload, user=p3_member)
        assert item.detected_content_type == "text/plain"
        assert item.storage_key.endswith(".txt")

    def test_binary_disguised_as_text_rejected(self, p3_member):
        upload = SimpleUploadedFile(
            "notes.txt", b"\x00\x01\x02binary", content_type="text/plain",
        )
        with pytest.raises(SubmissionError):
            prepare_file(upload, user=p3_member)

    def test_object_actually_stored(self, p3_member):
        """不只是返回对象 —— 文件应真的写进了私有存储。"""
        from core.storage import get_storage

        upload = SimpleUploadedFile("r.pdf", make_pdf(),
                                    content_type="application/pdf")
        item = prepare_file(upload, user=p3_member)
        store = get_storage()
        assert store.exists(item.storage_key)
        assert store.read(item.storage_key).startswith(b"%PDF")

    def test_original_name_sanitised(self, p3_member):
        """原始文件名仅作展示元数据，必须去掉路径成分。"""
        upload = SimpleUploadedFile("../../etc/passwd.pdf", make_pdf(),
                                    content_type="application/pdf")
        item = prepare_file(upload, user=p3_member)
        assert "/" not in item.original_name
        assert ".." not in item.original_name
        assert "/" not in item.storage_key and ".." not in item.storage_key

    def test_disallowed_extension_rejected(self, p3_member):
        upload = SimpleUploadedFile("evil.exe", b"%PDF-1.4\n" + b"x" * 100,
                                    content_type="application/pdf")
        with pytest.raises(SubmissionError) as exc:
            prepare_file(upload, user=p3_member)
        assert "仅支持" in str(exc.value)

    def test_fake_content_rejected(self, p3_member):
        """扩展名与魔数不符：内容是纯文本但叫 .pdf → 必须被拒。"""
        upload = SimpleUploadedFile("fake.pdf", b"just plain text" * 20,
                                    content_type="application/pdf")
        with pytest.raises(SubmissionError) as exc:
            prepare_file(upload, user=p3_member)
        assert "无法识别文件内容类型" in str(exc.value)

    def test_empty_file_rejected(self, p3_member):
        upload = SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf")
        with pytest.raises(SubmissionError):
            prepare_file(upload, user=p3_member)

    def test_oversize_rejected(self, p3_member, settings):
        settings.UPLOAD_LIMITS = {**getattr(settings, "UPLOAD_LIMITS", {}),
                                  "file_max_bytes": 1024}
        upload = SimpleUploadedFile("big.pdf", make_pdf() + b"x" * 2000,
                                    content_type="application/pdf")
        with pytest.raises(SubmissionError) as exc:
            prepare_file(upload, user=p3_member)
        assert "不能超过" in str(exc.value)


# ===========================================================================
# 材料类型允许性：文件走完整提交路径
# ===========================================================================

@pytest.mark.django_db
class TestAllowedFileSubmission:

    def _make_task_allowing(self, p3_admin, p3_worktype, competition, **flags):
        from tasks.models import CompetitionTask, TaskTemplate

        defaults = dict(allow_text=True, require_text=True,
                        allow_image=True, require_image=True)
        defaults.update(flags)
        tpl = TaskTemplate.objects.create(
            name="允许文件的模板", work_type=p3_worktype, created_by=p3_admin,
            max_records_per_member=1, **defaults)
        task = CompetitionTask.build_from_template(
            competition=competition, template=tpl, created_by=p3_admin)
        task.save()
        return task

    def test_submit_with_file(self, p3_admin, p3_worktype,
                              p3_competition, p3_member):
        """模板允许时，文件应能随提交一起入库。"""
        from submissions import orchestration
        from tasks.models import TaskAssignment

        task = self._make_task_allowing(
            p3_admin, p3_worktype, p3_competition,
            allow_file=True, require_file=True,
        )
        asg = TaskAssignment.objects.create(
            task=task, member=p3_member, assigned_by=p3_admin)

        class FakeFiles:
            def __init__(self, d):
                self._d = d

            def getlist(self, key):
                v = self._d.get(key, [])
                return v if isinstance(v, list) else [v]

            def get(self, key):
                v = self._d.get(key)
                if isinstance(v, list):
                    return v[0] if v else None
                return v

        files = FakeFiles({
            "images": [make_upload("i.jpg", make_image_bytes())],
            "files": [SimpleUploadedFile("r.pdf", make_pdf(),
                                         content_type="application/pdf")],
        })
        sub = orchestration.submit_new(
            assignment_id=asg.pk, user=p3_member,
            text_content="海报与报告都交了", post_data={}, files=files,
        )

        types = set(sub.current_version.attachments.values_list("material_type", flat=True))
        assert "file" in types, "文件附件应入库"
        assert "image" in types


# ===========================================================================
# 孤立对象清理命令
# ===========================================================================

@pytest.mark.django_db
class TestCleanupOrphansCommand:

    def test_command_registered(self):
        from django.core.management import get_commands

        assert "cleanup_orphans" in get_commands()

    def test_reports_nothing_when_empty(self, capsys):
        call_command("cleanup_orphans")
        out = capsys.readouterr().out
        assert "没有待清理" in out

    def test_deletes_orphan_object(self, isolated_private_storage):
        """已被上传但库里没记录的对象，命令应真的删掉并标记 resolved。"""
        from core.storage import get_storage

        store = get_storage()
        key = "orphan-probe.jpg"
        store.save(key, b"data")
        OrphanObject.objects.create(storage_key=key, reason="测试残留")

        assert store.exists(key)
        call_command("cleanup_orphans")

        assert not store.exists(key), "对象应被删除"
        orphan = OrphanObject.objects.get(storage_key=key)
        assert orphan.resolved is True
        assert orphan.resolved_at is not None

    def test_dry_run_keeps_object(self, isolated_private_storage):
        from core.storage import get_storage

        store = get_storage()
        key = "dryrun-probe.jpg"
        store.save(key, b"data")
        OrphanObject.objects.create(storage_key=key, reason="测试")

        call_command("cleanup_orphans", "--dry-run")

        assert store.exists(key), "dry-run 不得删除对象"
        assert OrphanObject.objects.get(storage_key=key).resolved is False

    def test_skips_object_still_referenced(self, submitted, isolated_private_storage):
        """若对象其实仍被附件引用，**不能删**（防误删正常数据）。"""
        from core.storage import get_storage

        att = Attachment.objects.filter(version__submission=submitted).first()
        store = get_storage()
        assert store.exists(att.storage_key)

        # 人为把它也记进孤立表
        OrphanObject.objects.create(storage_key=att.storage_key, reason="误记")

        call_command("cleanup_orphans")

        assert store.exists(att.storage_key), "被引用的对象不得删除"
        assert OrphanObject.objects.get(
            storage_key=att.storage_key).resolved is False

    def test_older_than_filter(self, isolated_private_storage):
        from datetime import timedelta

        from django.utils import timezone

        from core.storage import get_storage

        store = get_storage()
        old_key, new_key = "old-probe.jpg", "new-probe.jpg"
        store.save(old_key, b"a")
        store.save(new_key, b"b")
        old = OrphanObject.objects.create(storage_key=old_key, reason="旧")
        OrphanObject.objects.create(storage_key=new_key, reason="新")
        OrphanObject.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=5))

        call_command("cleanup_orphans", "--older-than", "3")

        assert not store.exists(old_key), "超过 3 天的应被清理"
        assert store.exists(new_key), "较新的不应被清理"


# ===========================================================================
# 附件下载：COS 后端（无本地路径）分支
# ===========================================================================

@pytest.mark.django_db
class TestDownloadWithoutLocalPath:
    """覆盖「存储后端没有本地路径」的分支。

    早期实现遇到这种情况会直接 404 —— 那意味着**生产用 COS 时附件根本下载不了**。
    现改为由后端读取对象内容后转发，两个后端行为一致。
    """

    def _client_and_url(self, client, submitted, p3_member):
        from django.urls import reverse

        att = Attachment.objects.filter(
            version__submission=submitted, is_thumbnail=False).first()
        client.force_login(p3_member)
        return att, reverse("submissions:attachment_download", args=[att.pk])

    def test_falls_back_to_backend_proxy(self, client, submitted, p3_member,
                                         monkeypatch):
        from django.urls import reverse

        from core import storage as storage_mod

        att = Attachment.objects.filter(
            version__submission=submitted, is_thumbnail=False).first()
        real_store = storage_mod.get_storage()

        class NoLocalPathStore:
            """模拟 COS：没有本地路径，只能 read()。"""

            def presigned_url(self, key, **kw):
                return "/files/%s?token=fake" % key

            def open_local_path(self, key):
                return None

            def read(self, key):
                return real_store.read(key)

            def exists(self, key):
                return True

        # 同上：打到 core.storage 上才生效
        monkeypatch.setattr(storage_mod, "get_storage", lambda: NoLocalPathStore())

        client.force_login(p3_member)
        # 直接访问私有文件视图（跳过签名跳转）
        from core.storage import get_storage

        # presigned_url 用了真实实现，这里换成手写路径避免签名不匹配
        from django.core import signing

        from core.storage import DOWNLOAD_SALT

        token = signing.dumps({"key": att.storage_key}, salt=DOWNLOAD_SALT,
                              compress=True)
        resp = client.get(reverse("core:private_file", kwargs={"key": att.storage_key}),
                          {"token": token})

        assert resp.status_code == 200, "COS 后端下应能下载（原先会 404）"
        body = b"".join(resp.streaming_content) if resp.streaming else resp.content
        assert len(body) > 0, "应有内容返回"

    def test_missing_object_returns_404_not_500(self, client, submitted,
                                                p3_member, monkeypatch):
        """存储里对象不存在时应 404，而不是 500。"""
        from django.urls import reverse

        from core import storage as storage_mod
        from core.storage import DOWNLOAD_SALT
        from django.core import signing

        att = Attachment.objects.filter(
            version__submission=submitted, is_thumbnail=False).first()

        class MissingStore:
            def presigned_url(self, key, **kw):
                return "/x"

            def open_local_path(self, key):
                return None

            def read(self, key):
                raise FileNotFoundError(key)

            def exists(self, key):
                return False

        # 视图内是「from core.storage import get_storage」的函数内导入，
        # 因此必须打到 core.storage.get_storage 上（打 core.views 不生效）
        monkeypatch.setattr(storage_mod, "get_storage", lambda: MissingStore())

        token = signing.dumps({"key": att.storage_key}, salt=DOWNLOAD_SALT,
                              compress=True)
        client.force_login(p3_member)
        resp = client.get(reverse("core:private_file", kwargs={"key": att.storage_key}),
                          {"token": token})
        assert resp.status_code == 404
