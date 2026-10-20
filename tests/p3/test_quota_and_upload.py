# -*- coding: utf-8 -*-
"""配额与上传测试（P3 要求逐条覆盖）。

重点在「并发不超额」和「失败不永久占配额」两条 ——
它们必须用真实多线程 + 真实数据库验证，而不是顺序模拟。
"""
import threading

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.upload_config import get_upload_limits
from core.storage import get_storage
from submissions import orchestration, services
from submissions.models import (
    Attachment,
    OrphanObject,
    Submission,
    UserImageUsage,
)
from submissions.pipeline import prepare_image, record_orphans

from .conftest import make_image_bytes, make_upload


# ===========================================================================
# 单文件大小与格式
# ===========================================================================

@pytest.mark.django_db
class TestSingleFileLimits:

    def test_image_over_limit_rejected(self, settings, p3_member):
        """超过单张图片上限（默认 10 MiB）必须被拒。"""
        limits = get_upload_limits()
        # 造一个「声明很大」的文件：直接构造超过上限的字节
        oversized = b"\xff\xd8\xff" + b"0" * (limits.image_max_bytes + 1024)
        upload = SimpleUploadedFile("big.jpg", oversized, content_type="image/jpeg")
        with pytest.raises(services.SubmissionError) as exc:
            prepare_image(upload, user=p3_member)
        assert "10 MiB" in str(exc.value) or "超过" in str(exc.value)

    def test_image_limit_is_configurable(self, settings, p3_member):
        """上限必须可通过配置修改，而不是写死在代码里。"""
        settings.UPLOAD_LIMITS = {**getattr(settings, "UPLOAD_LIMITS", {}),
                                  "image_max_bytes": 1024}
        upload = make_upload("small.jpg", make_image_bytes(size=(800, 600)))
        with pytest.raises(services.SubmissionError):
            prepare_image(upload, user=p3_member)

    def test_fake_image_rejected(self, p3_member):
        """伪造扩展名/Content-Type 的文本必须被拒。"""
        upload = SimpleUploadedFile(
            "fake.jpg", b"this is not an image" * 50, content_type="image/jpeg")
        with pytest.raises(services.SubmissionError):
            prepare_image(upload, user=p3_member)

    def test_empty_file_rejected(self, p3_member):
        upload = SimpleUploadedFile("empty.jpg", b"", content_type="image/jpeg")
        with pytest.raises(services.SubmissionError):
            prepare_image(upload, user=p3_member)

    def test_link_scheme_restricted(self):
        """作品链接只允许 http/https。"""
        for bad in ("javascript:alert(1)", "ftp://x.com/a", "file:///etc/passwd",
                    "data:text/html,<script>", "//example.com"):
            with pytest.raises(services.SubmissionError):
                from submissions.pipeline import prepare_link
                prepare_link(bad)

    def test_link_accepted(self):
        from submissions.pipeline import prepare_link

        for good in ("https://example.com/a", "http://example.com/b?c=1"):
            item = prepare_link(good)
            assert item.external_url == good
            assert item.size_bytes == 0        # 链接不占存储


# ===========================================================================
# 图片数量与容量配额
# ===========================================================================

@pytest.mark.django_db
class TestImageQuota:

    def _submit_with_images(self, assignment, user, count):
        class FakeFiles:
            def __init__(self, d):
                self._d = d

            def getlist(self, k):
                return self._d.get(k, [])

            def get(self, k):
                v = self._d.get(k)
                return v[0] if isinstance(v, list) and v else None

        return orchestration.submit_new(
            assignment_id=assignment.pk, user=user, text_content="内容",
            post_data={},
            files=FakeFiles({"images": [
                make_upload("t%d.jpg" % i, make_image_bytes(size=(900, 700)))
                for i in range(count)]}),
        )

    def test_usage_recorded_after_submit(self, p3_assignment, p3_member):
        assert UserImageUsage.objects.filter(user=p3_member).count() == 0
        self._submit_with_images(p3_assignment, p3_member, 1)
        usage = UserImageUsage.objects.get(user=p3_member)
        assert usage.image_count == 1
        assert usage.total_bytes > 0

    def test_usage_matches_attachments(self, p3_assignment, p3_member):
        """配额必须能依据现存附件重新核算（不能只靠计数器）。"""
        self._submit_with_images(p3_assignment, p3_member, 2)
        usage = UserImageUsage.objects.get(user=p3_member)
        recalculated = UserImageUsage.recalculate_from_attachments(p3_member)
        assert recalculated.image_count == usage.image_count
        assert recalculated.total_bytes == usage.total_bytes

    def test_thumbnail_not_counted_as_separate_image(self, p3_assignment, p3_member):
        """一次上传只算一张图 —— 主图与缩略图分两条记录但只计一次。"""
        self._submit_with_images(p3_assignment, p3_member, 1)

        images = Attachment.objects.filter(uploaded_by=p3_member, material_type="image")
        mains = images.filter(is_thumbnail=False)
        thumbs = images.filter(is_thumbnail=True)

        assert mains.count() == 1
        assert thumbs.count() == 1, "缩略图应作为独立对象存储"
        usage = UserImageUsage.objects.get(user=p3_member)
        assert usage.image_count == 1, "缩略图不得被算成第二张图片"

    def test_bytes_include_thumbnail(self, p3_assignment, p3_member):
        """存储量必须计入主图 + 缩略图的真实字节数。"""
        self._submit_with_images(p3_assignment, p3_member, 1)
        main = Attachment.objects.get(
            uploaded_by=p3_member, material_type="image", is_thumbnail=False)
        thumb = Attachment.objects.get(
            uploaded_by=p3_member, material_type="image", is_thumbnail=True)

        assert main.thumbnail_bytes == thumb.size_bytes
        usage = UserImageUsage.objects.get(user=p3_member)
        assert usage.total_bytes == main.size_bytes + thumb.size_bytes
        assert main.counted_bytes == main.size_bytes + main.thumbnail_bytes

    def test_count_limit_enforced(self, p3_assignment, p3_member, settings):
        """图片张数达上限时应拒绝，并给出当前限制。"""
        settings.UPLOAD_LIMITS = {**getattr(settings, "UPLOAD_LIMITS", {}),
                                  "images_per_user": 1}
        self._submit_with_images(p3_assignment, p3_member, 1)

        with pytest.raises(services.QuotaExceeded) as exc:
            self._submit_with_images(p3_assignment, p3_member, 2)
        message = str(exc.value)
        assert "1" in message and "张" in message, "应显示当前限制与用量：%s" % message
        assert exc.value.max_count == 1

    def test_bytes_limit_enforced(self, p3_assignment, p3_member, settings):
        """存储量达上限时应拒绝。"""
        settings.UPLOAD_LIMITS = {**getattr(settings, "UPLOAD_LIMITS", {}),
                                  "image_bytes_per_user": 1024}
        with pytest.raises(services.QuotaExceeded) as exc:
            self._submit_with_images(p3_assignment, p3_member, 1)
        assert exc.value.max_bytes == 1024

    def test_historical_versions_count_toward_quota(self, p3_assignment, p3_member,
                                                   p3_reviewer):
        """历史版本里的图片照样计入配额 —— 不得靠删旧版本规避。"""
        from django.db import transaction

        sub = self._submit_with_images(p3_assignment, p3_member, 1)
        before = UserImageUsage.objects.get(user=p3_member).image_count

        with transaction.atomic():
            services.reject_submission(
                submission=services.lock_submission(sub.pk),
                reviewer=p3_reviewer, comment="请重做")
        orchestration.submit_revision(
            submission_id=sub.pk, user=p3_member, text_content="第二版",
            post_data={},
            files=type("F", (), {
                "getlist": lambda self, k: [make_upload("v2.jpg", make_image_bytes())]
                if k == "images" else [],
                "get": lambda self, k: None,
            })(),
        )

        after = UserImageUsage.objects.get(user=p3_member).image_count
        assert after == before + 1, "重提新增的图片应累加，旧版本的也不应被扣除"
        # 第一版的附件仍然存在
        assert Attachment.objects.filter(version__version_no=1).exists()


# ===========================================================================
# 并发
# ===========================================================================

@pytest.mark.django_db(transaction=True)
class TestConcurrency:

    def test_concurrent_submit_respects_record_limit(self, p3_assignment, p3_member):
        """并发提交不能绕过「新增记录数上限」。

        用真实线程 + 真实 PostgreSQL 行级锁验证；
        普通的先查后写在这里会失败。
        """
        task = p3_assignment.task
        task.max_records_per_member = 1
        task.save(update_fields=["max_records_per_member"])

        results = {"ok": 0, "rejected": 0, "other": 0}

        def worker():
            # 每个线程用完必须关闭自己的数据库连接，否则连接会残留、
            # 占住测试库导致 teardown 失败（可能表现为偶发错误）
            from django.db import connection
            try:
                orchestration.submit_new(
                    assignment_id=p3_assignment.pk, user=p3_member,
                    text_content="并发", post_data={},
                    files=type("F", (), {
                        "getlist": lambda self, k: [make_upload("c.jpg", make_image_bytes())]
                        if k == "images" else [],
                        "get": lambda self, k: None,
                    })(),
                )
                results["ok"] += 1
            except services.SubmissionError:
                results["rejected"] += 1
            except Exception:  # noqa: BLE001
                results["other"] += 1
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        actual = Submission.objects.filter(assignment=p3_assignment).count()
        assert actual <= 1, "并发下不得超过上限，实际创建了 %d 条" % actual
        assert results["ok"] == actual == 1, "应恰好一个成功：%s" % results

    def test_concurrent_image_quota_not_bypassed(self, p3_assignment2, p3_member2,
                                                 settings):
        """并发上传不能绕过图片数量配额。"""
        settings.UPLOAD_LIMITS = {**getattr(settings, "UPLOAD_LIMITS", {}),
                                  "images_per_user": 1}
        task = p3_assignment2.task
        task.max_records_per_member = 5      # 让次数不成为限制，只测配额
        task.save(update_fields=["max_records_per_member"])

        errors = []

        def worker():
            from django.db import connection
            try:
                orchestration.submit_new(
                    assignment_id=p3_assignment2.pk, user=p3_member2,
                    text_content="并发", post_data={},
                    files=type("F", (), {
                        "getlist": lambda self, k: [make_upload("c.jpg", make_image_bytes())]
                        if k == "images" else [],
                        "get": lambda self, k: None,
                    })(),
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        usage = UserImageUsage.objects.get(user=p3_member2)
        assert usage.image_count <= 1, "并发下不得超过图片配额，实际 %d 张" % usage.image_count


# ===========================================================================
# 失败补偿
# ===========================================================================

@pytest.mark.django_db
class TestFailureCompensation:

    def test_failed_submission_does_not_leave_records(self, p3_assignment, p3_member):
        """数据库写入失败时，不得留下"已提交成功"的记录。"""
        before = Submission.objects.count()
        # 用超配额强制失败（在写库前抛出）
        from django.test import override_settings

        with override_settings(UPLOAD_LIMITS={"images_per_user": 0}):
            with pytest.raises(Exception):
                orchestration.submit_new(
                    assignment_id=p3_assignment.pk, user=p3_member,
                    text_content="会失败", post_data={},
                    files=type("F", (), {
                        "getlist": lambda self, k: [make_upload("f.jpg", make_image_bytes())]
                        if k == "images" else [],
                        "get": lambda self, k: None,
                    })(),
                )

        assert Submission.objects.count() == before, "失败不得留下记录"

    def test_failed_submission_does_not_consume_quota(self, p3_assignment, p3_member,
                                                      settings):
        """失败的上传不得永久占用配额。"""
        settings.UPLOAD_LIMITS = {**getattr(settings, "UPLOAD_LIMITS", {}),
                                  "images_per_user": 0}
        try:
            orchestration.submit_new(
                assignment_id=p3_assignment.pk, user=p3_member,
                text_content="会失败", post_data={},
                files=type("F", (), {
                    "getlist": lambda self, k: [make_upload("f.jpg", make_image_bytes())]
                    if k == "images" else [],
                    "get": lambda self, k: None,
                })(),
            )
        except Exception:  # noqa: BLE001
            pass

        usage = UserImageUsage.objects.filter(user=p3_member).first()
        assert usage is None or usage.image_count == 0, "失败不应占用配额"

    def test_orphan_recorded_when_cleanup_fails(self, db, p3_member, settings):
        """清理失败的对象必须记为待清理，不能假装已清理。"""
        # 造一个不存在的对象键：删除会返回 False（视为已清理）
        deleted, failed = __import__(
            "submissions.pipeline", fromlist=["cleanup_uploaded"]
        ).cleanup_uploaded(["nonexistent-key.jpg"])
        assert "nonexistent-key.jpg" in deleted      # 本就不存在，视为已清理

        # 现在模拟删除抛异常的情况
        from unittest import mock

        from submissions import pipeline

        with mock.patch.object(pipeline, "get_storage") as fake:
            fake.return_value.delete.side_effect = RuntimeError("storage down")
            deleted, failed = pipeline.cleanup_uploaded(["k1.jpg", "k2.jpg"])
        assert failed == ["k1.jpg", "k2.jpg"]

        count = record_orphans(failed, reason="测试：删除失败")
        assert count == 2
        assert OrphanObject.objects.filter(resolved=False).count() == 2
        assert all("k" in o.storage_key for o in OrphanObject.objects.all())

    def test_orphan_can_be_marked_resolved(self, db):
        obj = OrphanObject.objects.create(storage_key="x.jpg", reason="测试")
        obj.mark_resolved()
        obj.refresh_from_db()
        assert obj.resolved is True
        assert obj.resolved_at is not None


# ===========================================================================
# 存储与访问控制
# ===========================================================================

@pytest.mark.django_db
class TestPrivateStorage:

    def test_object_key_is_server_generated(self):
        """对象键必须由服务端生成，不含任何用户输入。"""
        import re

        from core.storage import generate_object_key

        for _ in range(20):
            key = generate_object_key()
            assert re.fullmatch(r"[0-9a-f]{32}\.jpg", key), key
            thumb = generate_object_key(thumb=True)
            assert re.fullmatch(r"[0-9a-f]{32}_thumb\.jpg", thumb), thumb

    def test_local_storage_rejects_path_traversal(self, isolated_private_storage):
        """即便有人传入可疑 key，也必须拒绝路径分隔符。"""
        from core.storage import StorageConfigError, get_storage

        store = get_storage()
        for bad in ("../escape.jpg", "a/b.jpg", "a\\b.jpg", "..\\x.jpg"):
            with pytest.raises(StorageConfigError):
                store.delete(bad)

    def test_production_rejects_default_data_dir(self, settings):
        """P5 规则变更：生产用本地磁盘时，数据目录不能停在开发默认值。

        原断言是「生产缺 COS 配置必须报错」（P3 时生产强制 COS）。
        P5 起改为服务器本地磁盘，该前提已不成立，故按新规则重写 ——
        这不是放松校验，而是把守的关口换成真正会丢数据的那一个：
        目录仍在项目内（容器里即临时层），容器重建附件就没了。
        """
        from pathlib import Path

        from core.storage import StorageConfigError, build_storage, reset_storage_cache

        settings.DEBUG = False
        settings.PRIVATE_STORAGE_BACKEND = "local"
        # 刻意指回开发默认值（BASE_DIR/private_media）
        settings.PRIVATE_MEDIA_ROOT = Path(settings.BASE_DIR) / "private_media"
        settings.PRIVATE_MEDIA_ROOT_IS_EXPLICIT = False
        reset_storage_cache()
        with pytest.raises(StorageConfigError) as exc:
            build_storage()
        assert "PRIVATE_MEDIA_ROOT" in str(exc.value)

    def test_production_accepts_explicit_persistent_dir(self, settings, tmp_path):
        """生产配好持久化数据目录后，本地后端应正常工作。"""
        from core.storage import build_storage, reset_storage_cache

        root = tmp_path / "srv-data" / "private_media"
        root.mkdir(parents=True)
        settings.DEBUG = False
        settings.PRIVATE_STORAGE_BACKEND = "local"
        settings.PRIVATE_MEDIA_ROOT = root
        settings.PRIVATE_MEDIA_ROOT_IS_EXPLICIT = True
        reset_storage_cache()
        store = build_storage()
        assert store.__class__.__name__ == "LocalPrivateStorage"
        assert store.root == root

    def test_production_requires_storage_root(self, settings):
        """生产环境完全没有数据目录配置时也必须报错。"""
        from core.storage import StorageConfigError, build_storage, reset_storage_cache

        settings.DEBUG = False
        settings.PRIVATE_STORAGE_BACKEND = "local"
        settings.PRIVATE_MEDIA_ROOT = None
        reset_storage_cache()
        with pytest.raises(StorageConfigError):
            build_storage()

    def test_unknown_backend_rejected(self, settings):
        """后端名写错时必须报错，不得猜成 local。"""
        from core.storage import StorageConfigError, build_storage, reset_storage_cache

        settings.PRIVATE_STORAGE_BACKEND = "s3"
        reset_storage_cache()
        with pytest.raises(StorageConfigError) as exc:
            build_storage()
        assert "PRIVATE_STORAGE_BACKEND" in str(exc.value)

    def test_cos_backend_requires_credentials(self, settings):
        """选了 COS 但凭据不全时必须明确报错。"""
        from core.storage import StorageConfigError, build_storage, reset_storage_cache

        settings.PRIVATE_STORAGE_BACKEND = "cos"
        settings.COS_CONFIG = {"region": "ap-beijing", "bucket": "",
                               "secret_id": "", "secret_key": ""}
        reset_storage_cache()
        with pytest.raises(StorageConfigError):
            build_storage()

    def test_presigned_url_is_signed_and_expires(self, isolated_private_storage, db):
        """本地实现的下载链接同样带签名，且会过期。"""
        from core.storage import get_storage, verify_download_token

        store = get_storage()
        key = "abc123.jpg"
        url = store.presigned_url(key)
        assert "token=" in url

        import urllib.parse

        token = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["token"][0]
        assert verify_download_token(token) == key

        # 篡改后的 token 必须无效
        assert verify_download_token(token[:-3] + "xxx") is None

    def test_expired_token_rejected(self, isolated_private_storage, settings):
        """过期后链接必须失效。"""
        from core.storage import get_storage, verify_download_token

        settings.UPLOAD_LIMITS = {**getattr(settings, "UPLOAD_LIMITS", {}),
                                  "presign_expires_seconds": 1}
        import time

        store = get_storage()
        url = store.presigned_url("exp.jpg")
        import urllib.parse

        token = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["token"][0]
        assert verify_download_token(token) == "exp.jpg"

        time.sleep(2)      # max_age 为 1 秒
        assert verify_download_token(token) is None, "过期链接必须失效"

    def test_files_stored_outside_public_dirs(self, isolated_private_storage, settings):
        """私有附件目录不得位于 STATIC_ROOT / MEDIA_ROOT 之下。"""
        from pathlib import Path

        private = Path(str(settings.PRIVATE_MEDIA_ROOT)).resolve()
        static = Path(str(settings.STATIC_ROOT)).resolve()
        media = Path(str(settings.MEDIA_ROOT)).resolve()
        assert private != static and static not in private.parents
        assert private != media and media not in private.parents
