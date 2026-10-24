"""
P2 权限测试。

对应验收「权限」一组，重点验证：
- 未登录不能进入受保护页面
- 干事不能创建比赛、编辑模板、分配任务
- 干事只能看到自己的有效任务，改 URL 也不行
- 未授权者不能管理审核授权
- **普通登录权不能推导出审核权**
- 管理员能完成规定范围内的管理操作
"""
import pytest
from django.urls import reverse

from competitions.models import ReviewerAssignment
from competitions.permissions import (
    can_manage_competition,
    can_manage_reviewers,
    can_review_competition,
    can_view_competition,
    visible_competitions_for,
)
from core.permissions import user_is_admin
from tasks.models import TaskAssignment
from tasks.permissions import assignments_for_member


# ===========================================================================
# 角色判定
# ===========================================================================

@pytest.mark.django_db
class TestRoleJudgement:

    def test_admin_role_grants_admin(self, admin_user):
        assert user_is_admin(admin_user) is True

    def test_member_is_not_admin(self, member_user):
        assert user_is_admin(member_user) is False

    def test_superuser_is_admin(self, superuser):
        assert user_is_admin(superuser) is True

    def test_authenticated_user_can_view_but_not_admin(self, regular_user):
        assert regular_user.is_authenticated is True
        assert user_is_admin(regular_user) is False, "普通查看不得等同管理权"

    def test_anonymous_has_nothing(self, db, django_user_model):
        from django.contrib.auth.models import AnonymousUser
        anon = AnonymousUser()
        assert user_is_admin(anon) is False

    def test_multiple_roles_supported(self, member_user, role_admin):
        """一个用户可同时是干事和管理员（多重身份）。"""
        member_user.roles.add(role_admin)
        codes = member_user.role_codes()
        assert "member" in codes and "club_admin" in codes
        assert user_is_admin(member_user) is True

    def test_inactive_role_grants_nothing(self, member_user, role_admin):
        role_admin.is_active = False
        role_admin.save(update_fields=["is_active"])
        member_user.roles.add(role_admin)
        assert user_is_admin(member_user) is False


# ===========================================================================
# 比赛可见性与管理权
# ===========================================================================

@pytest.mark.django_db
class TestCompetitionPermissions:

    def test_draft_hidden_from_member(self, draft_competition, member_user, admin_user):
        assert can_view_competition(member_user, draft_competition) is False
        assert can_view_competition(admin_user, draft_competition) is True

    def test_active_visible_to_all_logged_in(self, active_competition, member_user,
                                             regular_user):
        assert can_view_competition(member_user, active_competition) is True
        assert can_view_competition(regular_user, active_competition) is True

    def test_archived_still_visible(self, archived_competition, member_user):
        assert can_view_competition(member_user, archived_competition) is True

    def test_visible_queryset_excludes_draft_for_member(self, make_competition,
                                                        member_user, admin_user):
        make_competition(name="草稿", status="draft")
        make_competition(name="进行", status="active")
        make_competition(name="归档", status="archived")

        names = set(visible_competitions_for(member_user).values_list("name", flat=True))
        assert "草稿" not in names
        assert {"进行", "归档"} <= names

        admin_names = set(visible_competitions_for(admin_user).values_list("name", flat=True))
        assert "草稿" in admin_names

    def test_member_cannot_manage(self, active_competition, member_user):
        assert can_manage_competition(member_user, active_competition) is False

    def test_regular_user_cannot_manage(self, active_competition, regular_user):
        assert can_manage_competition(regular_user, active_competition) is False


# ===========================================================================
# 审核权：最关键的一条 —— 与其它权限互不推导
# ===========================================================================

@pytest.mark.django_db
class TestReviewPermissionIsolation:

    def test_admin_without_grant_cannot_review(self, active_competition, admin_user):
        """管理员身份本身不带来审核权。"""
        assert user_is_admin(admin_user) is True
        assert can_review_competition(admin_user, active_competition) is False

    def test_user_without_grant_cannot_review(self, active_competition, regular_user):
        """普通登录身份不推导审核权。"""
        assert can_review_competition(regular_user, active_competition) is False

    def test_superuser_without_grant_cannot_review(self, active_competition, superuser):
        assert can_review_competition(superuser, active_competition) is False

    def test_granted_user_can_review(self, active_competition, member_user, admin_user):
        ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        assert can_review_competition(member_user, active_competition) is True

    def test_revoked_grant_loses_review(self, active_competition, member_user, admin_user):
        a = ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        a.revoke()
        assert can_review_competition(member_user, active_competition) is False

    def test_grant_is_per_competition(self, make_competition, member_user, admin_user):
        """授权只对那一场比赛生效，不会顺延到别的比赛。"""
        c1 = make_competition(name="比赛甲", status="active")
        c2 = make_competition(name="比赛乙", status="active")
        ReviewerAssignment.objects.create(competition=c1, reviewer=member_user,
                                          granted_by=admin_user)

        assert can_review_competition(member_user, c1) is True
        assert can_review_competition(member_user, c2) is False

    def test_reviewer_cannot_manage_by_being_granted(
            self, active_competition, member_user, admin_user):
        """被授权审核不等于获得管理权。"""
        ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        assert can_review_competition(member_user, active_competition) is True
        assert can_manage_competition(member_user, active_competition) is False
        assert can_manage_reviewers(member_user, active_competition) is False


# ===========================================================================
# 干事端任务隔离
# ===========================================================================

@pytest.mark.django_db
class TestMemberTaskIsolation:

    def test_only_own_tasks(self, task, member_user, other_member, make_assignment):
        make_assignment(task, member_user)
        assert list(assignments_for_member(member_user)) == \
            list(TaskAssignment.objects.filter(task=task, member=member_user))
        assert assignments_for_member(other_member).count() == 0

    def test_inactive_assignment_not_shown(self, task, member_user, make_assignment):
        a = make_assignment(task, member_user)
        assert assignments_for_member(member_user).count() == 1
        a.deactivate()
        assert assignments_for_member(member_user).count() == 0

    def test_anonymous_gets_nothing(self, db, task):
        from django.contrib.auth.models import AnonymousUser
        anon = AnonymousUser()
        assert assignments_for_member(anon).count() == 0


# ===========================================================================
# 视图层权限（真实 HTTP 请求）
# ===========================================================================

@pytest.mark.django_db
class TestViewPermissions:

    # --- 未登录 ---

    @pytest.mark.parametrize("url_name,args", [
        ("competitions:list", []),
        ("tasks:my_tasks", []),
        ("tasks_admin:template_list", []),
        ("tasks_admin:worktype_list", []),
    ])
    def test_anonymous_redirected_to_login(self, client, url_name, args):
        url = reverse(url_name, args=args)
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    def test_anonymous_cannot_create_competition(self, client):
        resp = client.get(reverse("competitions:create"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    # --- 干事不得管理 ---

    def test_member_cannot_open_competition_create(self, logged_in_client, member_user):
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("competitions:create"))
        assert resp.status_code == 403

    def test_member_cannot_post_competition_create(self, logged_in_client, member_user):
        from competitions.models import Competition
        client = logged_in_client.as_(member_user)
        before = Competition.objects.count()
        resp = client.post(reverse("competitions:create"), {
            "name": "偷偷建的", "event_date": "2026-11-01",
            "default_deadline": "2026-11-02T18:00", "description": "",
        })
        assert resp.status_code == 403
        assert Competition.objects.count() == before, "不得真的创建成功"

    def test_member_cannot_edit_template(self, logged_in_client, member_user, template):
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("tasks_admin:template_update", args=[template.pk]))
        assert resp.status_code == 403

    def test_member_cannot_create_worktype(self, logged_in_client, member_user):
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("tasks_admin:worktype_create"))
        assert resp.status_code == 403

    def test_member_cannot_assign_task(self, logged_in_client, member_user, task):
        client = logged_in_client.as_(member_user)
        resp = client.post(reverse("tasks_admin:task_assign", args=[task.pk]),
                           {"members": [member_user.pk]})
        assert resp.status_code == 403
        assert TaskAssignment.objects.filter(task=task).count() == 0

    def test_member_cannot_open_task_manage_page(self, logged_in_client, member_user, task):
        """干事打开管理视角的任务详情应被拒（统一 403）。"""
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("tasks_admin:task_detail", args=[task.pk]))
        assert resp.status_code == 403

    def test_assigned_member_still_cannot_open_manage_page(
            self, logged_in_client, member_user, task, make_assignment):
        """即使该任务分配给了这名干事，管理端页面仍须 403。

        这条是回归护栏：早期实现会给"被分配的干事"做一个到
        /my/tasks/mine/<pk>/ 的友好跳转（302），导致权限边界不统一 ——
        其它管理页对干事都是 403，只有这一处"部分可用"。
        若日后有人把它改回重定向，本测试会立刻失败。
        """
        make_assignment(task, member_user)
        client = logged_in_client.as_(member_user)

        resp = client.get(reverse("tasks_admin:task_detail", args=[task.pk]))
        assert resp.status_code == 403, \
            "管理端任务页对干事必须一律 403，不能因该任务恰好分配给他而放行"

        # 干事的正确入口是干事端，且应可正常访问
        own = client.get(reverse("tasks:my_task_detail", args=[task.pk]))
        assert own.status_code == 200

    def test_regular_user_cannot_manage(self, logged_in_client, regular_user):
        client = logged_in_client.as_(regular_user)
        assert client.get(reverse("competitions:create")).status_code == 403
        assert client.get(reverse("tasks_admin:template_list")).status_code == 403

    # --- 干事不得看别人的任务 ---

    def test_member_cannot_view_others_assignment_via_url(
            self, logged_in_client, task, other_member, make_assignment):
        """直接改 URL 里的 task_pk 想偷看别人的任务 → 403。"""
        make_assignment(task, other_member)     # 分配给小王
        client = logged_in_client.as_(other_member)

        from django.contrib.auth import get_user_model
        intruder = get_user_model().objects.create_user(
            account="M0003", password="P2TestPassw0rd!2026", real_name="闯入者")
        intruder.must_change_password = False
        intruder.save(update_fields=["must_change_password"])

        client.force_login(intruder)
        resp = client.get(reverse("tasks:my_task_detail", args=[task.pk]))
        assert resp.status_code == 403

    def test_member_sees_own_task_detail(self, logged_in_client, task,
                                         member_user, make_assignment):
        make_assignment(task, member_user)
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("tasks:my_task_detail", args=[task.pk]))
        assert resp.status_code == 200

    def test_member_my_tasks_excludes_others(self, logged_in_client, make_task,
                                             make_template, member_user, other_member,
                                             make_assignment, template):
        make_assignment(make_task(template, name="我的任务"), member_user)
        other_template = make_template(name="别人的模板")
        make_assignment(make_task(other_template, name="别人的任务"), other_member)

        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("tasks:my_tasks"))
        body = resp.content.decode("utf-8")

        assert "我的工作" in body
        assert "别人的任务" not in body, "不得出现他人的任务"

    # --- 审核授权管理 ---

    def test_member_cannot_manage_reviewers(self, logged_in_client, member_user,
                                            active_competition):
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("competitions:reviewers", args=[active_competition.pk]))
        assert resp.status_code == 403

    def test_regular_user_cannot_manage_reviewers(self, logged_in_client, regular_user,
                                            active_competition):
        client = logged_in_client.as_(regular_user)
        resp = client.get(reverse("competitions:reviewers", args=[active_competition.pk]))
        assert resp.status_code == 403

    def test_granted_reviewer_still_cannot_manage_reviewers(
            self, logged_in_client, member_user, active_competition, admin_user):
        """被授权审核的人不能管理授权 —— 授权与审核分离。"""
        ReviewerAssignment.objects.create(
            competition=active_competition, reviewer=member_user, granted_by=admin_user)
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("competitions:reviewers", args=[active_competition.pk]))
        assert resp.status_code == 403

    def test_draft_not_visible_to_member(self, logged_in_client, member_user,
                                         draft_competition):
        client = logged_in_client.as_(member_user)
        resp = client.get(reverse("competitions:detail", args=[draft_competition.pk]))
        assert resp.status_code == 403, "草稿对干事必须不可见（按不存在处理）"

    # --- 管理员可正常操作 ---

    def test_admin_can_open_management_pages(self, logged_in_client, admin_user,
                                             template, active_competition):
        client = logged_in_client.as_(admin_user)
        for url in [
            reverse("competitions:list"),
            reverse("competitions:create"),
            reverse("competitions:detail", args=[active_competition.pk]),
            reverse("competitions:reviewers", args=[active_competition.pk]),
            reverse("tasks_admin:template_list"),
            reverse("tasks_admin:template_create"),
            reverse("tasks_admin:worktype_list"),
            reverse("tasks_admin:worktype_create"),
        ]:
            assert client.get(url).status_code == 200, url

    def test_admin_can_view_draft(self, logged_in_client, admin_user,
                                  draft_competition):
        client = logged_in_client.as_(admin_user)
        resp = client.get(reverse("competitions:detail", args=[draft_competition.pk]))
        assert resp.status_code == 200
