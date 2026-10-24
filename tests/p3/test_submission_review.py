# -*- coding: utf-8 -*-
"""提交、版本与审核闭环测试（P3 要求逐条覆盖）。"""
import threading

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from core.audit import AuditLog
from reviews.models import ReviewAction, ReviewLog
from submissions import orchestration, services
from submissions.models import Submission, SubmissionStatus, SubmissionVersion

from .conftest import make_image_bytes, make_upload


def files_with_images(*names):
    """构造含图片的 request.FILES 替身。"""
    class FakeFiles:
        def __init__(self, data):
            self._d = data

        def getlist(self, key):
            return self._d.get(key, [])

        def get(self, key):
            v = self._d.get(key)
            return v[0] if isinstance(v, list) and v else None

    return FakeFiles({"images": [
        make_upload(n, make_image_bytes(size=(900, 700))) for n in names]})


# ===========================================================================
# 首次提交
# ===========================================================================

@pytest.mark.django_db
class TestFirstSubmission:

    def test_submit_creates_record_and_version(self, p3_assignment, p3_member):
        sub = orchestration.submit_new(
            assignment_id=p3_assignment.pk, user=p3_member,
            text_content="完成海报", post_data={}, files=files_with_images("a.jpg"),
        )
        assert sub.status == SubmissionStatus.PENDING
        assert sub.edition_count == 1
        assert sub.current_version.version_no == 1
        assert sub.current_version.text_content == "完成海报"

    def test_requires_text_and_image(self, p3_assignment, p3_member):
        """模板要求文字 + 图片必填，缺任一项都必须被拒。"""
        # 缺图片
        class Empty:
            def getlist(self, k):
                return []

            def get(self, k):
                return None

        with pytest.raises(services.SubmissionError) as exc:
            orchestration.submit_new(
                assignment_id=p3_assignment.pk, user=p3_member,
                text_content="有文字没图片", post_data={}, files=Empty(),
            )
        assert "图片证明" in str(exc.value)

        # 缺文字
        with pytest.raises(services.SubmissionError) as exc:
            orchestration.submit_new(
                assignment_id=p3_assignment.pk, user=p3_member,
                text_content="   ", post_data={}, files=files_with_images("b.jpg"),
            )
        assert "文字说明" in str(exc.value)

    def test_cannot_submit_others_assignment(self, p3_assignment, p3_member2):
        """只能提交分配给自己的任务。"""
        with pytest.raises(services.SubmissionError) as exc:
            orchestration.submit_new(
                assignment_id=p3_assignment.pk, user=p3_member2,
                text_content="越权", post_data={}, files=files_with_images("c.jpg"),
            )
        assert "没有分配给您" in str(exc.value)

    def test_cannot_submit_inactive_assignment(self, p3_assignment, p3_member):
        p3_assignment.deactivate()
        with pytest.raises(services.SubmissionError):
            orchestration.submit_new(
                assignment_id=p3_assignment.pk, user=p3_member,
                text_content="已取消", post_data={}, files=files_with_images("d.jpg"),
            )

    def test_record_limit_enforced(self, p3_assignment, p3_member, p3_reviewer):
        """「每人每任务新增记录数」按记录数（不是版本数）限制。"""
        task = p3_assignment.task
        task.max_records_per_member = 1
        task.save(update_fields=["max_records_per_member"])

        orchestration.submit_new(
            assignment_id=p3_assignment.pk, user=p3_member,
            text_content="第一条", post_data={}, files=files_with_images("e.jpg"),
        )
        with pytest.raises(services.SubmissionError) as exc:
            orchestration.submit_new(
                assignment_id=p3_assignment.pk, user=p3_member,
                text_content="第二条", post_data={}, files=files_with_images("f.jpg"),
            )
        assert "最多提交 1 条" in str(exc.value)

    def test_overdue_flag_from_effective_deadline(self, p3_assignment, p3_member):
        """逾期标记依据提交时的生效截止时间。"""
        task = p3_assignment.task
        task.deadline = timezone.now() - timedelta(days=1)   # 任务独立截止已过
        task.save(update_fields=["deadline"])

        sub = orchestration.submit_new(
            assignment_id=p3_assignment.pk, user=p3_member,
            text_content="迟到提交", post_data={}, files=files_with_images("g.jpg"),
        )
        version = sub.current_version
        assert version.is_overdue is True
        assert version.effective_deadline == task.deadline
        assert sub.is_overdue_now is True

    def test_not_overdue_when_within_deadline(self, p3_assignment, p3_member):
        sub = orchestration.submit_new(
            assignment_id=p3_assignment.pk, user=p3_member,
            text_content="准时提交", post_data={}, files=files_with_images("h.jpg"),
        )
        assert sub.current_version.is_overdue is False

    def test_extra_material_not_allowed_rejected(self, p3_assignment, p3_member):
        """提交模板不允许的材料（作品链接）必须被拒。

        注意：这里用的是与真实 request.FILES 语义一致的替身 ——
        Django 的 MultiValueDict 里，`getlist(k)` 与 `get(k)` 都能取到同一个上传，
        测试替身必须同样如此，否则会测出"假失败"。
        """
        class FakeFiles:
            """与 request.FILES 语义一致的替身。"""

            def __init__(self, data):
                self._d = data

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
        })
        with pytest.raises(services.SubmissionError) as exc:
            orchestration.submit_new(
                assignment_id=p3_assignment.pk, user=p3_member,
                text_content="带链接", post_data={"link": "https://example.com"},
                files=files,
            )
        assert "不允许提交" in str(exc.value)
        assert "作品链接" in str(exc.value)

    def test_disallowed_material_not_uploaded(self, p3_assignment, p3_member):
        """被拒的材料**不应**被上传到存储（前置检查须早于上传）。

        这是本测试第一版暴露出的真实设计改进点：早期实现是"先上传、后校验"，
        会让被拒的材料白白占存储、还要额外补偿清理。
        """
        from core.storage import get_storage

        class FakeFiles:
            def __init__(self, data):
                self._d = data

            def getlist(self, key):
                v = self._d.get(key, [])
                return v if isinstance(v, list) else [v]

            def get(self, key):
                v = self._d.get(key)
                if isinstance(v, list):
                    return v[0] if v else None
                return v

        files = FakeFiles({
            "images": [make_upload("ok.jpg", make_image_bytes())],
        })
        with pytest.raises(services.SubmissionError):
            orchestration.submit_new(
                assignment_id=p3_assignment.pk, user=p3_member,
                text_content="带链接",
                post_data={"link": "https://example.com"},
                files=files,
            )

        # 存储里不应留下任何本次上传的对象
        store = get_storage()
        leaked = [k for k in getattr(store, "iter_keys", lambda: [])()]
        assert leaked == [], "被拒的材料不应被上传，但发现：%s" % leaked

    def test_snapshot_governs_not_template_current(self, p3_assignment, p3_member,
                                                   p3_template):
        """必填校验依据任务快照，而不是模板的当前值（P2 设计的延续）。"""
        # 把模板改成"不要求图片"，但任务快照仍要求图片
        p3_template.require_image = False
        p3_template.allow_image = False
        p3_template.save()

        class Empty:
            def getlist(self, k):
                return []

            def get(self, k):
                return None

        # 快照里 require_image 仍为 True，因此仍需图片
        with pytest.raises(services.SubmissionError):
            orchestration.submit_new(
                assignment_id=p3_assignment.pk, user=p3_member,
                text_content="只有文字", post_data={}, files=Empty(),
            )


# ===========================================================================
# 退回与重新提交
# ===========================================================================

@pytest.mark.django_db
class TestRevision:

    def test_reject_requires_comment(self, submitted, p3_reviewer):
        with pytest.raises(services.SubmissionError) as exc:
            with transaction.atomic():
                services.reject_submission(
                    submission=services.lock_submission(submitted.pk),
                    reviewer=p3_reviewer, comment="   ")
        assert "必须填写理由" in str(exc.value)

    def test_reject_sets_needs_revision(self, submitted, p3_reviewer):
        with transaction.atomic():
            services.reject_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="配色太暗，请调整")
        submitted.refresh_from_db()
        assert submitted.status == SubmissionStatus.NEEDS_REVISION
        assert submitted.can_be_revised is True

    def test_my_tasks_exposes_direct_revision_action(
            self, client, submitted, p3_member, p3_reviewer):
        with transaction.atomic():
            services.reject_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="请修改",
            )
        client.force_login(p3_member)
        response = client.get(reverse("tasks:my_tasks"))
        body = response.content.decode("utf-8")
        assert "待修改" in body
        assert reverse("submissions:submit_revision", args=[submitted.pk]) in body

    def test_revision_creates_new_version_only(self, submitted, p3_member, p3_reviewer):
        """重提产生新版本，且**不**新增工作记录（规则 6）。"""
        before = Submission.objects.filter(assignment=submitted.assignment).count()

        with transaction.atomic():
            services.reject_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="请修改")

        version = orchestration.submit_revision(
            submission_id=submitted.pk, user=p3_member,
            text_content="已修改", post_data={}, files=files_with_images("v2.jpg"),
        )
        after = Submission.objects.filter(assignment=submitted.assignment).count()

        assert after == before, "重提不得新增工作记录（不占次数）"
        assert version.version_no == 2
        submitted.refresh_from_db()
        assert submitted.status == SubmissionStatus.PENDING, "重提后应回到待审核"

    def test_old_version_and_attachments_preserved(self, submitted, p3_member,
                                                   p3_reviewer):
        """旧版本内容与材料不可被新版本覆盖。"""
        v1 = submitted.current_version
        v1_attachment_ids = set(v1.attachments.values_list("pk", flat=True))
        v1_text = v1.text_content

        with transaction.atomic():
            services.reject_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="改一下")

        v2 = orchestration.submit_revision(
            submission_id=submitted.pk, user=p3_member,
            text_content="第二版文字", post_data={},
            files=files_with_images("v2b.jpg"),
        )

        v1.refresh_from_db()
        assert v1.text_content == v1_text, "旧版本文字不得被覆盖"
        assert set(v1.attachments.values_list("pk", flat=True)) == v1_attachment_ids
        assert not v1_attachment_ids & set(v2.attachments.values_list("pk", flat=True))

    def test_version_numbers_continuous(self, submitted, p3_member, p3_reviewer):
        """版本号必须连续、唯一、可核对。"""
        for expected in (2, 3):
            with transaction.atomic():
                services.reject_submission(
                    submission=services.lock_submission(submitted.pk),
                    reviewer=p3_reviewer, comment="继续改")
            orchestration.submit_revision(
                submission_id=submitted.pk, user=p3_member,
                text_content="第 %d 版" % expected, post_data={},
                files=files_with_images("v%d.jpg" % expected),
            )

        numbers = list(submitted.versions.order_by("version_no")
                       .values_list("version_no", flat=True))
        assert numbers == [1, 2, 3]

    def test_revision_only_when_needs_revision(self, submitted, p3_member):
        """待审核状态下不能改（必须先被退回）。"""
        with pytest.raises(services.SubmissionError) as exc:
            orchestration.submit_revision(
                submission_id=submitted.pk, user=p3_member,
                text_content="偷偷改", post_data={}, files=files_with_images("x.jpg"),
            )
        assert "退回修改" in str(exc.value)

    def test_revision_only_by_owner(self, submitted, p3_member2, p3_reviewer):
        """只有本人能修改自己的记录。"""
        with transaction.atomic():
            services.reject_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="改")
        with pytest.raises(services.SubmissionError) as exc:
            orchestration.submit_revision(
                submission_id=submitted.pk, user=p3_member2,
                text_content="越权改", post_data={}, files=files_with_images("y.jpg"),
            )
        assert "不是您的工作记录" in str(exc.value)

    def test_version_number_unique_constraint(self, submitted):
        """同记录内版本号重复必须被数据库拒绝（最后一道防线）。"""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                SubmissionVersion.objects.create(
                    submission=submitted, version_no=1,
                    text_content="重复版本号",
                    submitted_by=submitted.assignment.member,
                    effective_deadline=timezone.now(),
                )


# ===========================================================================
# 审核
# ===========================================================================

@pytest.mark.django_db
class TestReview:

    def test_approve(self, submitted, p3_reviewer):
        with transaction.atomic():
            services.approve_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="很好")
        submitted.refresh_from_db()
        assert submitted.status == SubmissionStatus.APPROVED
        log = ReviewLog.objects.get(submission=submitted)
        assert log.action == ReviewAction.APPROVE
        assert log.comment == "很好"
        assert log.version_id == submitted.current_version_id

    def test_reject_writes_log_with_reason(self, submitted, p3_reviewer):
        with transaction.atomic():
            services.reject_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="字幕有错别字")
        log = ReviewLog.objects.get(submission=submitted)
        assert log.action == ReviewAction.REJECT
        assert log.comment == "字幕有错别字"
        assert log.from_status == SubmissionStatus.PENDING
        assert log.to_status == SubmissionStatus.NEEDS_REVISION

    def test_cannot_reject_without_comment_at_db_level(self, submitted, p3_reviewer):
        """空理由在数据库层也必须被拒（不只是服务层）。"""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ReviewLog.objects.create(
                    submission=submitted, version=submitted.current_version,
                    action=ReviewAction.REJECT, comment="", reviewer=p3_reviewer,
                )

    def test_cannot_reopen_without_reason_at_db_level(self, submitted, p3_reviewer):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ReviewLog.objects.create(
                    submission=submitted, version=submitted.current_version,
                    action=ReviewAction.REOPEN, reopen_reason="", reviewer=p3_reviewer,
                )

    def test_reviewer_cannot_review_own_submission(self, submitted, p3_member):
        """审核人不得审核自己的提交。"""
        from submissions.permissions import can_review_submission

        assert can_review_submission(p3_member, submitted) is False

    def test_unauthorized_cannot_review(self, submitted, p3_member2):
        """未授权审核人（即使有 member 角色）不能审核。"""
        from submissions.permissions import can_review_submission

        assert can_review_submission(p3_member2, submitted) is False

    def test_admin_without_grant_cannot_review(self, submitted, p3_admin):
        """管理员身份本身不等于审核权（延续 P2 的规则）。"""
        from submissions.permissions import can_review_submission

        assert can_review_submission(p3_admin, submitted) is False

    def test_granted_reviewer_can_review(self, submitted, p3_reviewer):
        from submissions.permissions import can_review_submission

        assert can_review_submission(p3_reviewer, submitted) is True

    def test_reopen_requires_approved(self, submitted, p3_reviewer):
        """只有已通过的记录才能发起重新审核。"""
        with pytest.raises(services.SubmissionError) as exc:
            with transaction.atomic():
                services.reopen_submission(
                    submission=services.lock_submission(submitted.pk),
                    reviewer=p3_reviewer, reason="原因",
                )
        assert "只有「审核通过」" in str(exc.value)

    def test_reopen_keeps_original_history(self, submitted, p3_reviewer):
        """重新审核必须保留原通过记录与完整历史。"""
        with transaction.atomic():
            services.approve_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="第一次通过")
        with transaction.atomic():
            services.reopen_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="需要复核",
                reason="发现疑点，需复核",
            )

        logs = ReviewLog.objects.filter(submission=submitted).order_by("acted_at")
        assert logs.count() == 2
        assert logs[0].action == ReviewAction.APPROVE      # 原记录仍在
        assert logs[0].comment == "第一次通过"
        assert logs[1].action == ReviewAction.REOPEN
        assert logs[1].reopen_reason == "发现疑点，需复核"
        submitted.refresh_from_db()
        assert submitted.status == SubmissionStatus.PENDING

    def test_reopen_writes_to_audit_log(self, submitted, p3_reviewer):
        """审核动作同时写入既有 AuditLog（复用 P2 体系，不另建日志）。"""
        with transaction.atomic():
            services.approve_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="通过")
        with transaction.atomic():
            services.reopen_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, reason="复核",
            )
        assert AuditLog.objects.filter(action="submission.reopen").exists()
        log = AuditLog.objects.filter(action="submission.reopen").first()
        assert log.actor_id == p3_reviewer.id, "审计必须记录操作人"

    def test_reopen_view_uses_service_and_writes_audit(
            self, client, submitted, p3_reviewer):
        with transaction.atomic():
            services.approve_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer,
                comment="第一次通过",
            )
        client.force_login(p3_reviewer)
        response = client.post(
            reverse("submissions:reopen_submission", args=[submitted.pk]),
            {"reopen_reason": "发现疑点", "comment": "请复核"},
            HTTP_HOST="127.0.0.1",
        )
        assert response.status_code == 302
        submitted.refresh_from_db()
        assert submitted.status == SubmissionStatus.PENDING
        assert AuditLog.objects.filter(
            action="submission.reopen",
            target_id=str(submitted.pk),
        ).exists()

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_reopen_only_one_succeeds(self, submitted, p3_reviewer):
        with transaction.atomic():
            services.approve_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer,
                comment="通过",
            )
        outcomes = []

        def worker():
            from django.db import connection
            try:
                with transaction.atomic():
                    services.reopen_submission(
                        submission=services.lock_submission(submitted.pk),
                        reviewer=p3_reviewer,
                        reason="并发复核",
                    )
                outcomes.append("ok")
            except services.SubmissionError:
                outcomes.append("rejected")
            except Exception as exc:  # noqa: BLE001
                outcomes.append(type(exc).__name__)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert outcomes.count("ok") == 1
        assert outcomes.count("rejected") == 1

    def test_concurrent_review_only_one_effective(self, submitted, p3_reviewer):
        """同一轮并发审核只产生一个有效结果。

        这里顺序模拟"状态已被他人更新"的场景：第二次审核会因为
        状态已变而被拒。真实并发由 test_concurrency 覆盖。
        """
        with transaction.atomic():
            services.approve_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="先通过")

        with pytest.raises(services.SubmissionError) as exc:
            with transaction.atomic():
                services.approve_submission(
                    submission=services.lock_submission(submitted.pk),
                    reviewer=p3_reviewer, comment="再来一次")
        assert "刚刚被他人更新" in str(exc.value)
        assert ReviewLog.objects.filter(submission=submitted).count() == 1


# ===========================================================================
# 并发审核
# ===========================================================================

@pytest.mark.django_db(transaction=True)
class TestConcurrentReview:

    def test_concurrent_approve_produces_single_result(self, submitted, p3_reviewer):
        """两个线程同时审核 → 只有一个有效结果。"""
        outcomes = {"ok": 0, "conflict": 0, "other": 0}

        def worker():
            from django.db import connection
            try:
                with transaction.atomic():
                    services.approve_submission(
                        submission=services.lock_submission(submitted.pk),
                        reviewer=p3_reviewer, comment="并发通过")
                outcomes["ok"] += 1
            except services.SubmissionError:
                outcomes["conflict"] += 1
            except Exception:  # noqa: BLE001
                outcomes["other"] += 1
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert outcomes["ok"] == 1, "只能有一个成功：%s" % outcomes
        assert ReviewLog.objects.filter(
            submission=submitted, action=ReviewAction.APPROVE).count() == 1


# ===========================================================================
# 归档
# ===========================================================================

@pytest.mark.django_db
class TestArchived:

    def test_revoked_member_role_hides_tasks_and_blocks_submission(
            self, p3_assignment, p3_member):
        from django.test import Client

        p3_member.roles.clear()
        client = Client(raise_request_exception=False)
        client.force_login(p3_member)
        body = client.get(
            reverse("tasks:my_tasks"), HTTP_HOST="127.0.0.1",
        ).content.decode("utf-8")
        assert p3_assignment.task.name not in body
        with pytest.raises(services.SubmissionError, match="没有干事权限"):
            orchestration.submit_new(
                assignment_id=p3_assignment.pk,
                user=p3_member,
                text_content="撤销后",
                post_data={"text_content": "撤销后"},
                files=files_with_images("revoked.jpg"),
            )

    def test_cancelled_assignment_cannot_revise_existing_record(
            self, client, submitted, p3_member, p3_reviewer):
        with transaction.atomic():
            services.reject_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="修改",
            )
        submitted.assignment.is_active = False
        submitted.assignment.save(update_fields=["is_active", "deactivated_at"])
        client.force_login(p3_member)
        page = client.get(
            reverse("submissions:submit_revision", args=[submitted.pk]),
            HTTP_HOST="127.0.0.1",
        )
        assert page.status_code == 403
        with pytest.raises(services.SubmissionError, match="分配已被取消"):
            orchestration.submit_revision(
                submission_id=submitted.pk,
                user=p3_member,
                text_content="不应成功",
                post_data={"text_content": "不应成功"},
                files=files_with_images("cancelled.jpg"),
            )

    def test_archived_task_not_shown_as_current_todo(
            self, client, submitted, p3_member, p3_competition, p3_admin):
        p3_competition.archive(by_user=p3_admin)
        client.force_login(p3_member)
        body = client.get(reverse("tasks:my_tasks")).content.decode("utf-8")
        assert submitted.task.name not in body
        history = client.get(reverse("submissions:my_submissions"))
        assert history.status_code == 200
        assert submitted.task.name in history.content.decode("utf-8")

    def test_regular_member_cannot_see_reviewer_identity(
            self, client, p3_member, p3_member2, p3_competition):
        from competitions.models import ReviewerAssignment

        ReviewerAssignment.objects.create(
            competition=p3_competition,
            reviewer=p3_member,
            granted_by=p3_member,
        )
        client.force_login(p3_member2)
        body = client.get(
            reverse("competitions:detail", args=[p3_competition.pk]),
            HTTP_HOST="127.0.0.1",
        ).content.decode("utf-8")
        assert p3_member.real_name not in body

    def test_archived_blocks_new_submission(self, p3_assignment, p3_member,
                                            p3_competition, p3_admin):
        p3_competition.archive(by_user=p3_admin)
        with pytest.raises(services.SubmissionError) as exc:
            orchestration.submit_new(
                assignment_id=p3_assignment.pk, user=p3_member,
                text_content="归档后", post_data={}, files=files_with_images("z.jpg"),
            )
        assert "已归档" in str(exc.value)

    def test_archived_allows_revision_of_existing(self, submitted, p3_member,
                                                  p3_reviewer, p3_competition,
                                                  p3_admin):
        """归档后既有记录仍可退回并重提（规则 13）。"""
        with transaction.atomic():
            services.reject_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="改一下")
        p3_competition.archive(by_user=p3_admin)

        version = orchestration.submit_revision(
            submission_id=submitted.pk, user=p3_member,
            text_content="归档后重提", post_data={},
            files=files_with_images("after.jpg"),
        )
        assert version.version_no == 2

    def test_archived_allows_review_of_existing(self, submitted, p3_reviewer,
                                                p3_competition, p3_admin):
        """归档后既有记录仍可完成审核。"""
        p3_competition.archive(by_user=p3_admin)
        with transaction.atomic():
            services.approve_submission(
                submission=services.lock_submission(submitted.pk),
                reviewer=p3_reviewer, comment="归档后审核")
        submitted.refresh_from_db()
        assert submitted.status == SubmissionStatus.APPROVED

    def test_archive_does_not_delete_history(self, submitted, p3_competition,
                                             p3_admin):
        """归档不得删除提交版本或附件。"""
        from submissions.models import Attachment

        versions_before = submitted.versions.count()
        attachments_before = Attachment.objects.filter(
            version__submission=submitted).count()

        p3_competition.archive(by_user=p3_admin)

        assert submitted.versions.count() == versions_before
        assert Attachment.objects.filter(
            version__submission=submitted).count() == attachments_before
        assert submitted.versions.exists()
