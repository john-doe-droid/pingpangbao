"""
P2 数据模型与约束测试。

对应验收「数据模型与约束」一组：
- 比赛创建/修改/归档
- 工作类型与模板的创建/修改/停用
- 任务保存模板规则快照
- 截止时间继承与优先
- 同一任务可分配多人、不重复分配同一人
- 审核人授权的创建/撤销/约束
- 已使用对象停用后历史仍保留
"""
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from competitions.models import (
    Competition,
    CompetitionStatus,
    ReviewerAssignment,
)
from tasks.models import (
    CompetitionTask,
    TaskAssignment,
    TaskTemplate,
    WorkType,
)


# ===========================================================================
# 比赛
# ===========================================================================

@pytest.mark.django_db
class TestCompetition:

    def test_create_with_defaults(self, admin_user):
        c = Competition.objects.create(
            name="新生杯", event_date=timezone.localdate(),
            default_deadline=timezone.now() + timezone.timedelta(days=5),
            created_by=admin_user,
        )
        assert c.status == CompetitionStatus.DRAFT
        assert c.pk is not None
        assert c.archived_at is None

    def test_archived_requires_timestamp(self, db):
        """数据库约束：状态为已归档但没归档时间必须被拒绝。"""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Competition.objects.create(
                    name="坏数据", event_date=timezone.localdate(),
                    default_deadline=timezone.now(),
                    status=CompetitionStatus.ARCHIVED,
                    archived_at=None,
                )

    def test_empty_name_rejected(self, db):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Competition.objects.create(
                    name="", event_date=timezone.localdate(),
                    default_deadline=timezone.now(),
                )

    def test_archive_sets_metadata(self, active_competition, admin_user):
        assert active_competition.is_archived is False
        changed = active_competition.archive(by_user=admin_user)
        active_competition.refresh_from_db()

        assert changed is True
        assert active_competition.status == CompetitionStatus.ARCHIVED
        assert active_competition.archived_at is not None
        assert active_competition.archived_by == admin_user

    def test_archive_is_idempotent(self, active_competition, admin_user):
        active_competition.archive(by_user=admin_user)
        assert active_competition.archive(by_user=admin_user) is False

    def test_archived_stops_task_configuration(self, archived_competition):
        assert archived_competition.can_modify_tasks is False

    def test_only_active_allows_task_configuration(self, active_competition, draft_competition):
        assert active_competition.can_modify_tasks is True
        assert draft_competition.can_modify_tasks is False

    def test_archive_does_not_delete_data(self, make_task, template,
                                          active_competition, admin_user):
        """归档是状态变更，不是删除：任务与分配必须仍在。"""
        task = make_task(template)
        active_competition.archive(by_user=admin_user)

        assert Competition.objects.filter(pk=active_competition.pk).exists()
        assert CompetitionTask.objects.filter(pk=task.pk).exists()

    def test_querysets(self, make_competition):
        make_competition(name="草稿", status=CompetitionStatus.DRAFT)
        make_competition(name="进行", status=CompetitionStatus.ACTIVE)
        make_competition(name="归档", status=CompetitionStatus.ARCHIVED)

        assert Competition.objects.draft().count() == 1
        assert Competition.objects.active().count() == 1
        assert Competition.objects.archived().count() == 1
        # 干事可见 = 非草稿
        assert Competition.objects.visible_to_members().count() == 2
        # 可配置任务 = 仅进行中
        assert Competition.objects.open_for_tasks().count() == 1


# ===========================================================================
# 工作类型
# ===========================================================================

@pytest.mark.django_db
class TestWorkType:

    def test_name_is_unique(self, make_worktype):
        make_worktype(name="推文排版")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                WorkType.objects.create(name="推文排版")

    def test_new_type_needs_no_code_change(self, db, admin_user):
        """新增工作类型只是插一行数据 —— 不需要改代码或重新部署。"""
        before = WorkType.objects.count()
        WorkType.objects.create(name="视频剪辑", created_by=admin_user)
        assert WorkType.objects.count() == before + 1

    def test_deactivate_keeps_history(self, make_worktype, make_template):
        wt = make_worktype(name="比赛摄影")
        template = make_template(name="摄影模板", work_type=wt)
        task_worktype = template.work_type

        wt.deactivate()
        wt.refresh_from_db()
        template.refresh_from_db()

        assert wt.is_active is False
        assert template.work_type_id == wt.pk
        assert template.work_type.name == task_worktype.name

    def test_reactivate(self, make_worktype):
        wt = make_worktype(is_active=False)
        wt.activate()
        wt.refresh_from_db()
        assert wt.is_active is True

    def test_worktype_in_use_cannot_be_deleted(self, make_worktype, make_template):
        """被模板引用的工作类型不能物理删除（PROTECT）。"""
        wt = make_worktype(name="被引用的类型")
        make_template(name="模板", work_type=wt)
        with pytest.raises(Exception):
            wt.delete()


# ===========================================================================
# 任务模板
# ===========================================================================

@pytest.mark.django_db
class TestTaskTemplate:

    def test_defaults(self, worktype, admin_user):
        t = TaskTemplate.objects.create(
            name="默认模板", work_type=worktype, created_by=admin_user)
        assert t.allow_text is True and t.require_text is True
        assert t.allow_image is True and t.require_image is True
        assert t.allow_file is False and t.require_file is False
        assert t.max_records_per_member == 1
        assert t.is_active is True

    def test_required_implies_allowed_at_form_level(self, worktype, admin_user):
        t = TaskTemplate(
            name="矛盾模板", work_type=worktype, created_by=admin_user,
            allow_image=False, require_image=True,
        )
        with pytest.raises(ValidationError) as exc:
            t.full_clean()
        assert "allow_image" in exc.value.message_dict

    def test_required_implies_allowed_at_db_level(self, worktype, admin_user):
        """绕过表单直接写库也必须被数据库约束挡住。"""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TaskTemplate.objects.create(
                    name="绕过表单", work_type=worktype, created_by=admin_user,
                    allow_video=False, require_video=True,
                )

    def test_max_records_must_be_positive_db(self, worktype, admin_user):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TaskTemplate.objects.create(
                    name="次数为零", work_type=worktype, created_by=admin_user,
                    max_records_per_member=0,
                )

    def test_max_records_validation(self, worktype, admin_user):
        t = TaskTemplate(
            name="次数非法", work_type=worktype, created_by=admin_user,
            max_records_per_member=0,
        )
        with pytest.raises(ValidationError) as exc:
            t.full_clean()
        assert "max_records_per_member" in exc.value.message_dict

    def test_max_records_can_be_adjusted(self, worktype, admin_user):
        t = TaskTemplate.objects.create(
            name="五份", work_type=worktype, created_by=admin_user,
            max_records_per_member=5,
        )
        assert t.max_records_per_member == 5

    def test_deactivate_and_history(self, make_template, make_task):
        template = make_template(name="将停用")
        task = make_task(template)
        template.deactivate()
        template.refresh_from_db()
        task.refresh_from_db()

        assert template.is_active is False
        assert task.template_id == template.pk
        assert task.pk is not None

    def test_material_matrix_labels(self, template):
        rows = dict((label, (allow, require)) for _k, label, allow, require
                    in template.material_matrix())
        assert rows["文字说明"] == (True, True)
        assert rows["图片证明"] == (True, True)
        assert rows["文件附件"] == (False, False)

    def test_rule_snapshot_content(self, worktype, admin_user):
        t = TaskTemplate.objects.create(
            name="快照模板", work_type=worktype, created_by=admin_user,
            allow_link=True, require_link=True, max_records_per_member=3,
        )
        snap = t.rule_snapshot()
        assert snap["allow_link"] is True
        assert snap["require_link"] is True
        assert snap["max_records_per_member"] == 3


# ===========================================================================
# 比赛任务：规则快照与截止时间
# ===========================================================================

@pytest.mark.django_db
class TestCompetitionTask:

    def test_snapshot_frozen_after_template_change(self, worktype, admin_user,
                                                   make_template, make_task):
        """模板改了，已创建任务的规则不能跟着变 —— 这是 P2 的硬要求。"""
        template = make_template(
            name="原始要求", work_type=worktype,
            allow_image=True, require_image=True,
            allow_video=False, require_video=False,
            max_records_per_member=1,
        )
        task = make_task(template)

        # 现在把模板改成完全不同的要求
        template.allow_image = False
        template.require_image = False
        template.allow_video = True
        template.require_video = True
        template.max_records_per_member = 9
        template.save()

        task.refresh_from_db()
        snap = task.rule_snapshot

        assert snap["require_image"] is True, "快照不应被模板修改影响"
        assert snap["allow_image"] is True
        assert snap["require_video"] is False, "模板新增的要求不应污染旧任务"
        assert task.max_records_per_member == 1
        assert template.max_records_per_member == 9

    def test_deadline_inherits_from_competition(self, template, make_task,
                                                active_competition):
        task = make_task(template)          # 未设置独立截止时间
        assert task.deadline is None
        assert task.effective_deadline == active_competition.default_deadline
        assert task.deadline_source == "继承自比赛"

    def test_task_deadline_overrides_competition(self, template, make_task,
                                                 active_competition):
        own = timezone.now() + timezone.timedelta(days=1)
        task = make_task(template, deadline=own)
        assert task.effective_deadline == own
        assert task.effective_deadline != active_competition.default_deadline
        assert task.deadline_source == "任务独立设置"

    def test_is_overdue(self, template, make_template, make_task, active_competition):
        # 注意：同一比赛下同一模板只能建一个任务（有意设计的约束），
        # 因此这里用两个不同模板，而不是放宽约束。
        tpl_expired = make_template(name="过期任务模板")
        tpl_future = make_template(name="未过期任务模板")

        past = timezone.now() - timezone.timedelta(days=1)
        task = make_task(tpl_expired, deadline=past, name="已过期任务")
        assert task.is_overdue() is True

        future = timezone.now() + timezone.timedelta(days=1)
        task2 = make_task(tpl_future, deadline=future, name="未过期任务")
        assert task2.is_overdue() is False

    def test_same_template_not_duplicated_in_one_competition(
            self, template, make_task, active_competition):
        make_task(template)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CompetitionTask.objects.create(
                    competition=active_competition, template=template,
                    name="重复任务", work_type=template.work_type,
                    rule_snapshot=template.rule_snapshot(),
                )

    def test_same_template_allowed_in_different_competitions(
            self, template, make_task, make_competition):
        c1 = make_competition(name="比赛甲", status=CompetitionStatus.ACTIVE)
        c2 = make_competition(name="比赛乙", status=CompetitionStatus.ACTIVE)
        t1 = make_task(template, competition=c1)
        t2 = make_task(template, competition=c2)
        assert t1.pk != t2.pk

    def test_snapshot_summary_excludes_disallowed(self, worktype, admin_user,
                                                  make_template, make_task):
        template = make_template(
            name="只要图片", work_type=worktype,
            allow_text=False, require_text=False,
            allow_image=True, require_image=True,
        )
        task = make_task(template)
        summary = task.snapshot_summary()
        assert "图片证明" in summary
        assert "文字说明" not in summary


# ===========================================================================
# 人员分配
# ===========================================================================

@pytest.mark.django_db
class TestTaskAssignment:

    def test_multiple_members_on_one_task(self, task, member_user, other_member,
                                          make_assignment):
        a1 = make_assignment(task, member_user)
        a2 = make_assignment(task, other_member)
        assert task.assignments.filter(is_active=True).count() == 2
        assert a1.pk != a2.pk

    def test_duplicate_member_rejected_by_db(self, task, member_user, make_assignment):
        """数据库部分唯一索引：同一任务不能对同一人存在两条有效分配。"""
        make_assignment(task, member_user)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TaskAssignment.objects.create(
                    task=task, member=member_user, is_active=True)

    def test_deactivating_allows_reassignment(self, task, member_user, make_assignment):
        """停用后可重新分配，但历史记录保留。"""
        first = make_assignment(task, member_user)
        first.deactivate()
        second = make_assignment(task, member_user)

        assert first.is_active is False
        assert second.is_active is True
        assert TaskAssignment.objects.filter(
            task=task, member=member_user).count() == 2, "历史必须保留"

    def test_deactivated_not_in_active_queryset(self, task, member_user, make_assignment):
        a = make_assignment(task, member_user)
        a.deactivate()
        assert task.assignments.active().count() == 0

    def test_deactivate_is_idempotent(self, task, member_user, make_assignment):
        a = make_assignment(task, member_user)
        assert a.deactivate() is True
        assert a.deactivate() is False

    def test_deactivate_records_timestamp(self, task, member_user, make_assignment):
        a = make_assignment(task, member_user)
        a.deactivate()
        a.refresh_from_db()
        assert a.deactivated_at is not None


# ===========================================================================
# 审核人授权
# ===========================================================================

@pytest.mark.django_db
class TestReviewerAssignment:

    def test_grant_multiple_reviewers(self, active_competition, member_user,
                                      other_member, admin_user):
        ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=other_member, granted_by=admin_user)
        assert active_competition.reviewer_assignments.filter(is_active=True).count() == 2

    def test_same_reviewer_not_duplicated(self, active_competition, member_user,
                                          admin_user):
        ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ReviewerAssignment.objects.create(
                    competition=active_competition, reviewer=member_user,
                    granted_by=admin_user, is_active=True)

    def test_revoke_keeps_history(self, active_competition, member_user, admin_user):
        a = ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        assert a.revoke() is True
        a.refresh_from_db()

        assert a.is_active is False
        assert a.revoked_at is not None
        assert ReviewerAssignment.objects.filter(pk=a.pk).exists(), "记录必须保留"

    def test_revoke_is_idempotent(self, active_competition, member_user, admin_user):
        a = ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        a.revoke()
        assert a.revoke() is False

    def test_regrant_after_revoke(self, active_competition, member_user, admin_user):
        a = ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        a.revoke()

        b = ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        assert b.is_active is True
        assert ReviewerAssignment.objects.filter(
            competition=active_competition, reviewer=member_user).count() == 2

    def test_one_reviewer_can_review_many_competitions(
            self, make_competition, member_user, admin_user):
        c1 = make_competition(name="比赛甲", status=CompetitionStatus.ACTIVE)
        c2 = make_competition(name="比赛乙", status=CompetitionStatus.ACTIVE)
        ReviewerAssignment.objects.create(competition=c1, reviewer=member_user,
                                          granted_by=admin_user)
        ReviewerAssignment.objects.create(competition=c2, reviewer=member_user,
                                          granted_by=admin_user)
        assert ReviewerAssignment.objects.filter(reviewer=member_user, is_active=True).count() == 2

    def test_inactive_user_relationship_survives(self, active_competition,
                                                 member_user, admin_user):
        """停用账号后，既有授权关系不消失。"""
        ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        member_user.is_active = False
        member_user.save(update_fields=["is_active"])

        assert ReviewerAssignment.objects.filter(
            competition=active_competition, reviewer=member_user, is_active=True).exists()
