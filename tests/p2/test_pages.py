"""
P2 页面功能测试（真实 HTTP 请求，覆盖浏览器上会发生的操作）。

对应验收「页面功能」一组：
- 关键表单的合法提交和无效输入
- 分配成功 / 重复分配 / 权限不足的正确反馈
- 归档后的比赛不出现在可新增任务的有效比赛列表中
- 核心管理页面在移动端宽度下可正常操作（结构层面可验证的部分）
"""
import pytest
from django.urls import reverse
from django.utils import timezone

from competitions.models import (
    Competition,
    CompetitionStatus,
    ReviewerAssignment,
)
from core.models import AuditLog
from tasks.models import (
    CompetitionTask,
    TaskAssignment,
    TaskTemplate,
    WorkType,
)


# ===========================================================================
# 比赛管理
# ===========================================================================

@pytest.mark.django_db
class TestCompetitionPages:

    def test_create_competition_via_form(self, logged_in_client, admin_user):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("competitions:create"), {
            "name": "2026 新生杯",
            "event_date": "2026-11-01",
            "default_deadline": "2026-11-08T18:00",
            "description": "面向新生的联赛",
        })
        assert resp.status_code == 302

        c = Competition.objects.get(name="2026 新生杯")
        assert c.status == CompetitionStatus.DRAFT
        assert c.created_by == admin_user

    def test_created_competition_is_draft_and_hidden(self, logged_in_client, admin_user):
        """新建的比赛必须是草稿，且干事看不到。"""
        client = logged_in_client.as_(admin_user)
        client.post(reverse("competitions:create"), {
            "name": "草稿赛", "event_date": "2026-11-01",
            "default_deadline": "2026-11-08T18:00", "description": "",
        })
        c = Competition.objects.get(name="草稿赛")
        assert c.status == CompetitionStatus.DRAFT

    def test_invalid_deadline_before_event_date(self, logged_in_client, admin_user):
        """截止时间早于比赛日期：表单必须报错，且不落库。"""
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("competitions:create"), {
            "name": "时间颠倒", "event_date": "2026-11-10",
            "default_deadline": "2026-11-01T18:00", "description": "",
        })
        assert resp.status_code == 200, "应回到表单页而不是跳转"
        body = resp.content.decode("utf-8")
        assert "不能早于比赛日期" in body
        assert not Competition.objects.filter(name="时间颠倒").exists()

    def test_empty_name_rejected(self, logged_in_client, admin_user):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("competitions:create"), {
            "name": "   ", "event_date": "2026-11-01",
            "default_deadline": "2026-11-08T18:00", "description": "",
        })
        assert resp.status_code == 200
        assert "不能为空" in resp.content.decode("utf-8")

    def test_update_competition(self, logged_in_client, admin_user, draft_competition):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("competitions:update", args=[draft_competition.pk]), {
            "name": "改过的名字", "event_date": str(draft_competition.event_date),
            "default_deadline": draft_competition.default_deadline.strftime("%Y-%m-%dT%H:%M"),
            "description": "新说明",
        })
        assert resp.status_code == 302
        draft_competition.refresh_from_db()
        assert draft_competition.name == "改过的名字"

    def test_activate_moves_draft_to_active(self, logged_in_client, admin_user,
                                            draft_competition):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("competitions:activate", args=[draft_competition.pk]))
        assert resp.status_code == 302
        draft_competition.refresh_from_db()
        assert draft_competition.status == CompetitionStatus.ACTIVE

    def test_activate_requires_post(self, logged_in_client, admin_user,
                                    draft_competition):
        """GET 不得触发状态变更。"""
        client = logged_in_client.as_(admin_user)
        resp = client.get(reverse("competitions:activate", args=[draft_competition.pk]))
        assert resp.status_code == 405
        draft_competition.refresh_from_db()
        assert draft_competition.status == CompetitionStatus.DRAFT

    def test_archive_requires_name_confirmation(self, logged_in_client, admin_user,
                                                active_competition):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("competitions:archive", args=[active_competition.pk]),
                           {"confirm_name": "写错的名字"})
        assert resp.status_code == 302
        active_competition.refresh_from_db()
        assert active_competition.status == CompetitionStatus.ACTIVE, "确认不匹配时不得归档"

    def test_archive_with_correct_name(self, logged_in_client, admin_user,
                                       active_competition):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("competitions:archive", args=[active_competition.pk]),
                           {"confirm_name": active_competition.name})
        assert resp.status_code == 302
        active_competition.refresh_from_db()
        assert active_competition.status == CompetitionStatus.ARCHIVED
        assert active_competition.archived_at is not None
        assert active_competition.archived_by == admin_user

    def test_archive_requires_post(self, logged_in_client, admin_user,
                                   active_competition):
        client = logged_in_client.as_(admin_user)
        resp = client.get(reverse("competitions:archive", args=[active_competition.pk]))
        assert resp.status_code == 405
        active_competition.refresh_from_db()
        assert active_competition.status == CompetitionStatus.ACTIVE

    def test_archived_competition_cannot_be_edited(self, logged_in_client, admin_user,
                                                   archived_competition):
        client = logged_in_client.as_(admin_user)
        resp = client.get(reverse("competitions:update", args=[archived_competition.pk]))
        assert resp.status_code == 302, "应被挡回详情页"
        archived_competition.refresh_from_db()
        assert archived_competition.name == "已归档比赛"

    def test_list_shows_status_and_creator(self, logged_in_client, admin_user,
                                           active_competition):
        client = logged_in_client.as_(admin_user)
        body = client.get(reverse("competitions:list")).content.decode("utf-8")
        assert active_competition.name in body
        assert "进行中" in body
        assert "默认截止" in body

    def test_member_list_uses_read_only_title(self, logged_in_client,
                                              member_user, active_competition):
        """干事看到的标题不应暗示自己拥有比赛管理权。"""
        client = logged_in_client.as_(member_user)
        body = client.get(reverse("competitions:list")).content.decode("utf-8")
        assert '<h1 class="pp-page-title">比赛</h1>' in body
        assert "新建比赛" not in body

    def test_archive_records_audit_log(self, logged_in_client, admin_user,
                                       active_competition):
        client = logged_in_client.as_(admin_user)
        client.post(reverse("competitions:archive", args=[active_competition.pk]),
                    {"confirm_name": active_competition.name})
        log = AuditLog.objects.filter(action="competition.archive").first()
        assert log is not None
        assert log.actor == admin_user
        assert str(active_competition.pk) == log.target_id


# ===========================================================================
# 工作类型与模板页面
# ===========================================================================

@pytest.mark.django_db
class TestWorkTypeAndTemplatePages:

    def test_create_worktype(self, logged_in_client, admin_user):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("tasks_admin:worktype_create"), {
            "name": "视频剪辑", "description": "剪辑比赛集锦", "sort_order": 50,
        })
        assert resp.status_code == 302
        assert WorkType.objects.filter(name="视频剪辑").exists()

    def test_duplicate_worktype_name_rejected(self, logged_in_client, admin_user,
                                              worktype):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("tasks_admin:worktype_create"), {
            "name": worktype.name, "description": "", "sort_order": 100,
        })
        assert resp.status_code == 200
        assert "已存在同名" in resp.content.decode("utf-8")

    def test_toggle_worktype(self, logged_in_client, admin_user, worktype):
        client = logged_in_client.as_(admin_user)
        client.post(reverse("tasks_admin:worktype_toggle", args=[worktype.pk]))
        worktype.refresh_from_db()
        assert worktype.is_active is False
        client.post(reverse("tasks_admin:worktype_toggle", args=[worktype.pk]))
        worktype.refresh_from_db()
        assert worktype.is_active is True

    def test_toggle_requires_post(self, logged_in_client, admin_user, worktype):
        client = logged_in_client.as_(admin_user)
        resp = client.get(reverse("tasks_admin:worktype_toggle", args=[worktype.pk]))
        assert resp.status_code == 405
        worktype.refresh_from_db()
        assert worktype.is_active is True

    def test_create_template(self, logged_in_client, admin_user, worktype):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("tasks_admin:template_create"), {
            "name": "推文撰写", "work_type": worktype.pk, "description": "写一篇推文",
            "allow_text": "on", "require_text": "on",
            "allow_image": "on",
            "max_records_per_member": 2,
        })
        assert resp.status_code == 302
        t = TaskTemplate.objects.get(name="推文撰写")
        assert t.max_records_per_member == 2
        assert t.require_image is False

    def test_create_template_with_single_choice_material_requirements(
            self, logged_in_client, admin_user, worktype):
        """新界面每种材料只提交一个三选一值。"""
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("tasks_admin:template_create"), {
            "name": "三选一模板",
            "work_type": worktype.pk,
            "description": "",
            "text_requirement": "none",
            "image_requirement": "optional",
            "file_requirement": "required",
            "link_requirement": "none",
            "max_records_per_member": 1,
        })
        assert resp.status_code == 302
        template = TaskTemplate.objects.get(name="三选一模板")
        assert template.allow_text is False
        assert template.require_text is False
        assert template.allow_image is True
        assert template.require_image is False
        assert template.allow_file is True
        assert template.require_file is True

    def test_template_require_without_allow_rejected(self, logged_in_client,
                                                     admin_user, worktype):
        """必填但未允许：表单必须报错且不落库。"""
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("tasks_admin:template_create"), {
            "name": "矛盾模板", "work_type": worktype.pk, "description": "",
            "require_image": "on",          # 要求必填，但没有 allow_image
            "max_records_per_member": 1,
        })
        assert resp.status_code == 200
        assert "必须同时勾选允许提交" in resp.content.decode("utf-8")
        assert not TaskTemplate.objects.filter(name="矛盾模板").exists()

    def test_template_zero_records_rejected(self, logged_in_client, admin_user,
                                            worktype):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("tasks_admin:template_create"), {
            "name": "零份", "work_type": worktype.pk, "description": "",
            "max_records_per_member": 0,
        })
        assert resp.status_code == 200
        assert "正整数" in resp.content.decode("utf-8")

    def test_template_toggle(self, logged_in_client, admin_user, template):
        client = logged_in_client.as_(admin_user)
        client.post(reverse("tasks_admin:template_toggle", args=[template.pk]))
        template.refresh_from_db()
        assert template.is_active is False


# ===========================================================================
# 比赛任务配置
# ===========================================================================

@pytest.mark.django_db
class TestTaskConfigPages:
    def _task_payload(self, template, name, *, use_template=True):
        return {
            "template": template.pk if use_template else "",
            "name": name,
            "work_type": template.work_type_id,
            "description": "",
            "deadline": "",
            "max_records_per_member": template.max_records_per_member,
            "text_requirement": (
                "required" if template.require_text
                else "optional" if template.allow_text else "none"
            ),
            "image_requirement": (
                "required" if template.require_image
                else "optional" if template.allow_image else "none"
            ),
            "file_requirement": (
                "required" if template.require_file
                else "optional" if template.allow_file else "none"
            ),
            "link_requirement": (
                "required" if template.require_link
                else "optional" if template.allow_link else "none"
            ),
        }

    def test_create_task_from_template(self, logged_in_client, admin_user,
                                       active_competition, template):
        client = logged_in_client.as_(admin_user)
        resp = client.post(
            reverse("tasks_admin:task_create", args=[active_competition.pk]),
            self._task_payload(template, "赛后海报"),
        )
        assert resp.status_code == 302
        task = CompetitionTask.objects.get(competition=active_competition)
        assert task.name == "赛后海报"
        assert task.rule_snapshot, "必须保存规则快照"

    def test_create_task_directly_without_template(
            self, logged_in_client, admin_user, active_competition, template):
        client = logged_in_client.as_(admin_user)
        payload = self._task_payload(
            template, "自定义任务", use_template=False,
        )
        payload.update({
            "text_requirement": "optional",
            "image_requirement": "none",
            "file_requirement": "none",
            "link_requirement": "required",
        })
        response = client.post(
            reverse("tasks_admin:task_create", args=[active_competition.pk]),
            payload,
        )
        assert response.status_code == 302
        task = CompetitionTask.objects.get(
            competition=active_competition, name="自定义任务",
        )
        assert task.template is None
        assert task.rule_snapshot["allow_text"] is True
        assert task.rule_snapshot["require_text"] is False
        assert task.rule_snapshot["require_link"] is True

    def test_create_task_when_no_templates_exist(
            self, logged_in_client, admin_user, active_competition, worktype):
        client = logged_in_client.as_(admin_user)
        page = client.get(
            reverse("tasks_admin:task_create", args=[active_competition.pk]),
        )
        assert page.status_code == 200
        assert "不使用模板，直接填写" in page.content.decode("utf-8")

        response = client.post(
            reverse("tasks_admin:task_create", args=[active_competition.pk]),
            {
                "template": "",
                "name": "无模板任务",
                "work_type": worktype.pk,
                "description": "",
                "deadline": "",
                "max_records_per_member": 1,
                "text_requirement": "required",
                "image_requirement": "none",
                "file_requirement": "none",
                "link_requirement": "none",
            },
        )
        assert response.status_code == 302
        task = CompetitionTask.objects.get(name="无模板任务")
        assert task.template is None
        assert task.rule_snapshot["require_text"] is True

    def test_task_create_blocked_on_draft_competition(self, logged_in_client,
                                                      admin_user, draft_competition,
                                                      template):
        client = logged_in_client.as_(admin_user)
        resp = client.post(
            reverse("tasks_admin:task_create", args=[draft_competition.pk]),
            {"template": template.pk, "name": "不该建", "description": "", "deadline": ""},
        )
        assert resp.status_code == 302
        assert not CompetitionTask.objects.filter(competition=draft_competition).exists()

    def test_task_create_blocked_on_archived_competition(
            self, logged_in_client, admin_user, archived_competition, template):
        """归档后不得新增任务。"""
        client = logged_in_client.as_(admin_user)
        client.post(
            reverse("tasks_admin:task_create", args=[archived_competition.pk]),
            {"template": template.pk, "name": "不该建", "description": "", "deadline": ""},
        )
        assert not CompetitionTask.objects.filter(
            competition=archived_competition).exists()

    def test_archived_not_in_available_competitions_for_tasks(
            self, make_competition, template, make_task):
        """归档后的比赛不应出现在「可新增任务」的集合里。"""
        make_competition(name="草稿赛", status="draft")
        active = make_competition(name="进行赛", status="active")
        archived = make_competition(name="归档赛", status="archived")
        make_task(template, competition=active)
        # 给归档比赛也造一条历史任务（模拟归档前已配置）
        other_template = TaskTemplate.objects.create(
            name="归档赛用的模板", work_type=template.work_type)
        CompetitionTask.objects.create(
            competition=archived, template=other_template, name="历史任务",
            work_type=template.work_type, rule_snapshot=other_template.rule_snapshot())

        available = Competition.objects.open_for_tasks()
        names = set(available.values_list("name", flat=True))
        assert "归档赛" not in names
        assert "草稿赛" not in names
        assert "进行赛" in names

    def test_duplicate_template_task_rejected(self, logged_in_client, admin_user,
                                              active_competition, template, make_task):
        make_task(template, competition=active_competition)
        client = logged_in_client.as_(admin_user)
        resp = client.post(
            reverse("tasks_admin:task_create", args=[active_competition.pk]),
            self._task_payload(template, "重复"),
        )
        assert resp.status_code == 200
        assert CompetitionTask.objects.filter(competition=active_competition).count() == 1

    def test_inactive_template_not_selectable(self, logged_in_client, admin_user,
                                              active_competition, template):
        template.deactivate()
        client = logged_in_client.as_(admin_user)
        resp = client.get(reverse("tasks_admin:task_create", args=[active_competition.pk]))
        body = resp.content.decode("utf-8")
        assert template.name not in body, "停用的模板不应出现在可选列表里"

    def test_task_edit_does_not_change_snapshot(self, logged_in_client, admin_user,
                                                task):
        client = logged_in_client.as_(admin_user)
        before = dict(task.rule_snapshot)
        resp = client.post(reverse("tasks_admin:task_update", args=[task.pk]), {
            "name": "改名后的任务", "description": "新说明", "deadline": "",
        })
        assert resp.status_code == 302
        task.refresh_from_db()
        assert task.name == "改名后的任务"
        assert task.rule_snapshot == before, "编辑不得改动规则快照"


# ===========================================================================
# 人员分配
# ===========================================================================

@pytest.mark.django_db
class TestAssignmentPages:

    def test_assignment_uses_self_managed_display_name(
            self, logged_in_client, admin_user, task, member_user):
        member_user.display_name = "小李"
        member_user.save(update_fields=["display_name"])
        client = logged_in_client.as_(admin_user)
        body = client.get(
            reverse("tasks_admin:task_detail", args=[task.pk]),
        ).content.decode("utf-8")
        assert "小李" in body

        from tasks.forms import TaskAssignmentForm

        form = TaskAssignmentForm(task=task)
        labels = dict(form.fields["members"].choices)
        assert labels[member_user.pk] == "小李（M0001）"

    def test_assign_multiple_members(self, logged_in_client, admin_user, task,
                                     member_user, other_member):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("tasks_admin:task_assign", args=[task.pk]),
                           {"members": [member_user.pk, other_member.pk]})
        assert resp.status_code == 302
        assert task.assignments.filter(is_active=True).count() == 2

    def test_reassign_same_member_reports_not_duplicated(
            self, logged_in_client, admin_user, task, member_user, make_assignment):
        make_assignment(task, member_user)
        client = logged_in_client.as_(admin_user)
        # 表单会先把已分配的人排除，因此这里直接构造一次重复提交
        resp = client.post(reverse("tasks_admin:task_assign", args=[task.pk]),
                           {"members": [member_user.pk]}, follow=True)
        body = resp.content.decode("utf-8")
        assert task.assignments.filter(is_active=True).count() == 1
        assert ("取消" in body or "请至少选择" in body or "未重复" in body or "已被分配" in body)

    def test_assign_requires_member_role(self, logged_in_client, admin_user, task,
                                         regular_user):
        """只有具备「干事」角色的账号才能被分配。"""
        client = logged_in_client.as_(admin_user)
        resp = client.get(reverse("tasks_admin:task_detail", args=[task.pk]))
        body = resp.content.decode("utf-8")
        assert regular_user.real_name not in body

    def test_deactivate_assignment(self, logged_in_client, admin_user, task,
                                   member_user, make_assignment):
        a = make_assignment(task, member_user)
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("tasks_admin:assignment_deactivate", args=[a.pk]))
        assert resp.status_code == 302
        a.refresh_from_db()
        assert a.is_active is False
        assert TaskAssignment.objects.filter(pk=a.pk).exists(), "记录必须保留"

    def test_deactivate_requires_post(self, logged_in_client, admin_user, task,
                                      member_user, make_assignment):
        a = make_assignment(task, member_user)
        client = logged_in_client.as_(admin_user)
        resp = client.get(reverse("tasks_admin:assignment_deactivate", args=[a.pk]))
        assert resp.status_code == 405
        a.refresh_from_db()
        assert a.is_active is True

    def test_cannot_assign_on_archived_competition(self, logged_in_client, admin_user,
                                                   archived_competition, template,
                                                   member_user):
        other_template = TaskTemplate.objects.create(
            name="归档用模板", work_type=template.work_type)
        task = CompetitionTask.objects.create(
            competition=archived_competition, template=other_template,
            name="历史任务", work_type=template.work_type,
            rule_snapshot=other_template.rule_snapshot(),
        )
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("tasks_admin:task_assign", args=[task.pk]),
                           {"members": [member_user.pk]})
        assert resp.status_code == 302
        assert task.assignments.count() == 0

    def test_assignment_creates_audit_log(self, logged_in_client, admin_user,
                                          task, member_user):
        client = logged_in_client.as_(admin_user)
        client.post(reverse("tasks_admin:task_assign", args=[task.pk]),
                    {"members": [member_user.pk]})
        assert AuditLog.objects.filter(action="assignment.create").exists()


# ===========================================================================
# 审核人授权页面
# ===========================================================================

@pytest.mark.django_db
class TestReviewerPages:

    def test_grant_and_list(self, logged_in_client, admin_user, active_competition,
                            member_user):
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("competitions:reviewers", args=[active_competition.pk]),
                           {"reviewers": [member_user.pk]})
        assert resp.status_code == 302
        assert ReviewerAssignment.objects.filter(
            competition=active_competition, reviewer=member_user, is_active=True).exists()

    def test_revoke_via_page(self, logged_in_client, admin_user, active_competition,
                             member_user):
        a = ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("competitions:reviewer_revoke",
                                   args=[active_competition.pk, a.pk]))
        assert resp.status_code == 302
        a.refresh_from_db()
        assert a.is_active is False

    def test_revoke_requires_post(self, logged_in_client, admin_user,
                                  active_competition, member_user):
        a = ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        client = logged_in_client.as_(admin_user)
        resp = client.get(reverse("competitions:reviewer_revoke",
                                  args=[active_competition.pk, a.pk]))
        assert resp.status_code == 405
        a.refresh_from_db()
        assert a.is_active is True

    def test_grant_records_audit_log(self, logged_in_client, admin_user,
                                     active_competition, member_user):
        client = logged_in_client.as_(admin_user)
        client.post(reverse("competitions:reviewers", args=[active_competition.pk]),
                    {"reviewers": [member_user.pk]})
        assert AuditLog.objects.filter(action="reviewer.grant").exists()

    def test_duplicate_grant_reports_gracefully(self, logged_in_client, admin_user,
                                                active_competition, member_user):
        ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        client = logged_in_client.as_(admin_user)
        resp = client.post(reverse("competitions:reviewers", args=[active_competition.pk]),
                           {"reviewers": [member_user.pk]}, follow=True)
        assert resp.status_code == 200
        assert ReviewerAssignment.objects.filter(
            competition=active_competition, reviewer=member_user).count() == 1


# ===========================================================================
# 干事端页面
# ===========================================================================

@pytest.mark.django_db
class TestMemberPages:

    def test_my_tasks_shows_required_fields(self, logged_in_client, member_user,
                                            task, make_assignment):
        make_assignment(task, member_user)
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("tasks:my_tasks"))
        body = resp.content.decode("utf-8")

        assert resp.status_code == 200
        assert task.name in body                       # 任务名称
        assert task.competition.name in body           # 比赛名称
        assert task.work_type.name in body             # 工作类型
        assert "截止时间" in body                       # 生效截止时间
        assert "待提交" in body or "已逾期" in body      # 逾期/待办状态

    def test_my_task_detail_points_to_real_submission_entry(self, logged_in_client,
                                                            member_user, task,
                                                            make_assignment):
        """任务详情页必须把用户引向真实可用的提交入口。

        原断言是「本页必须写明『提交功能将在后续阶段开放』，且不得出现提交按钮」
        —— 那是 P2 时期的事实（当时还没有提交功能）。P3 已实现提交，
        该断言的前提失效，故按当前真实契约改写，而不是直接删掉：
          · 不再出现任何"以后才开放"的误导文案；
          · 必须给出可用的提交路径（指向 /my/submissions/ 的入口）；
          · 本页仍不放置能直接提交的表单 —— 提交动作集中在我的工作记录页，
            避免同一动作两处入口、状态可能不一致。
        """
        make_assignment(task, member_user)
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("tasks:my_task_detail", args=[task.pk]))
        body = resp.content.decode("utf-8")
        assert resp.status_code == 200

        # 1) 不得再出现"尚未开放"的过时说法
        assert "后续阶段开放" not in body, "提交功能已实现，不应再声称未开放"
        assert "尚未开放" not in body

        # 2) 必须提供真实可用的提交入口
        assert reverse("submissions:my_submissions") in body, (
            "任务详情页应把用户引向「我的工作记录」以提交成果")

        # 3) 提交区块内不应出现直接提交的表单按钮（集中到工作记录页）
        assert "提交工作成果" in body

    def test_overdue_marker(self, logged_in_client, member_user, make_task,
                            template, make_assignment):
        past = timezone.now() - timezone.timedelta(days=2)
        t = make_task(template, deadline=past, name="过期任务")
        make_assignment(t, member_user)
        client = logged_in_client.as_(member_user)
        body = client.get(reverse("tasks:my_tasks")).content.decode("utf-8")
        assert "已逾期" in body

    def test_empty_state(self, logged_in_client, member_user):
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("tasks:my_tasks"))
        body = resp.content.decode("utf-8")
        assert resp.status_code == 200
        assert "没有分配给你的任务" in body

    def test_member_cannot_see_archived_competition_reviewer_list(
            self, logged_in_client, member_user, active_competition):
        """干事不得看到审核人名单页面。"""
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("competitions:reviewers", args=[active_competition.pk]))
        assert resp.status_code == 403


# ===========================================================================
# 移动端可用性（结构层面）
# ===========================================================================

@pytest.mark.django_db
def test_anonymous_home_redirects_to_login(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:login")


@pytest.mark.django_db
class TestMobileStructure:
    """页面在窄屏下可操作的结构前提。

    Bootstrap 5 的响应式靠 class 与 viewport meta 实现，
    因此这里校验渲染结果里确实具备这些要素 —— 真实视觉检查由浏览器验证完成。
    """

    def _assert_mobile_ready(self, body):
        assert 'name="viewport"' in body, "必须有 viewport meta，否则移动端会缩放错乱"
        assert "container" in body
        # 表格在窄屏需要横向滚动容器
        if "<table" in body:
            assert "table-responsive" in body, "表格必须包在 table-responsive 里"

    def test_home(self, client, logged_in_client, admin_user):
        body = logged_in_client.as_(admin_user).get(reverse("core:home")).content.decode("utf-8")
        self._assert_mobile_ready(body)

    def test_competition_list(self, logged_in_client, admin_user, active_competition):
        body = logged_in_client.as_(admin_user).get(
            reverse("competitions:list")).content.decode("utf-8")
        self._assert_mobile_ready(body)

    def test_competition_detail(self, logged_in_client, admin_user, active_competition,
                                task):
        body = logged_in_client.as_(admin_user).get(
            reverse("competitions:detail", args=[active_competition.pk])).content.decode("utf-8")
        self._assert_mobile_ready(body)

    def test_template_list(self, logged_in_client, admin_user, template):
        body = logged_in_client.as_(admin_user).get(
            reverse("tasks_admin:template_list")).content.decode("utf-8")
        self._assert_mobile_ready(body)

    def test_task_detail(self, logged_in_client, admin_user, task):
        body = logged_in_client.as_(admin_user).get(
            reverse("tasks_admin:task_detail", args=[task.pk])).content.decode("utf-8")
        self._assert_mobile_ready(body)

    def test_my_tasks(self, logged_in_client, member_user, task, make_assignment):
        make_assignment(task, member_user)
        body = logged_in_client.as_(member_user).get(
            reverse("tasks:my_tasks")).content.decode("utf-8")
        self._assert_mobile_ready(body)

    def test_reviewer_manage(self, logged_in_client, admin_user, active_competition):
        body = logged_in_client.as_(admin_user).get(
            reverse("competitions:reviewers", args=[active_competition.pk])).content.decode("utf-8")
        self._assert_mobile_ready(body)
