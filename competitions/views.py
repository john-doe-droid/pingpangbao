"""
competitions 视图：比赛管理与审核人授权。

安全要点：
- 所有写操作都是 POST + CSRF（Django 表单默认带 CSRF 中间件）；
- 每个视图都做**对象级**权限检查，不信任 URL 里的主键；
- 归档必须显式确认（输入比赛名称），并记录操作人与时间；
- 不使用物理删除：已使用的比赛不能删。
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from core.audit import log_action
from core.permissions import ROLE_MEMBER, admin_required, login_required_view, user_is_admin

from .forms import ArchiveCompetitionForm, CompetitionForm
from .models import Competition, CompetitionStatus, ReviewerAssignment
from .permissions import (
    can_manage_competition,
    can_manage_reviewers,
    can_review_competition,
    can_view_competition,
    reviewer_assignments_for,
    visible_competitions_for,
)


# ---------------------------------------------------------------------------
# 比赛列表
# ---------------------------------------------------------------------------

@login_required_view
def competition_list(request):
    """比赛列表。

    可见范围由权限层决定：
    - 管理员：全部（含草稿）
    - 其他登录用户：仅非草稿
    """
    qs = visible_competitions_for(request.user)

    status_filter = request.GET.get("status", "").strip()
    valid_status = {s.value for s in CompetitionStatus}
    if status_filter in valid_status:
        qs = qs.filter(status=status_filter)

    qs = qs.select_related("created_by").annotate(
        task_count=Count("tasks", distinct=True),
    )

    context = {
        "competitions": qs,
        "status_choices": CompetitionStatus.choices,
        "current_status": status_filter,
        "can_manage": user_is_admin(request.user),
        "page_title": "比赛管理",
    }
    return render(request, "competitions/competition_list.html", context)


# ---------------------------------------------------------------------------
# 创建 / 修改
# ---------------------------------------------------------------------------

@admin_required
@require_http_methods(["GET", "POST"])
def competition_create(request):
    if request.method == "POST":
        form = CompetitionForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                competition = form.save(commit=False)
                competition.created_by = request.user
                competition.status = CompetitionStatus.DRAFT
                competition.save()
                log_action(request, "competition.create", competition)
            messages.success(
                request,
                "比赛「%s」已创建为草稿。确认无误后请点击「开始进行」，"
                "之后才能配置任务和分配人员。" % competition.name,
            )
            return redirect("competitions:detail", pk=competition.pk)
        messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = CompetitionForm()

    return render(request, "competitions/competition_form.html", {
        "form": form, "page_title": "新建比赛", "is_create": True,
    })


@admin_required
@require_http_methods(["GET", "POST"])
def competition_update(request, pk):
    competition = get_object_or_404(Competition, pk=pk)

    if not can_manage_competition(request.user, competition):
        raise PermissionDenied("无权修改该比赛。")

    if competition.is_archived:
        messages.error(request, "已归档的比赛不能修改。如需变更，请联系管理员评估。")
        return redirect("competitions:detail", pk=competition.pk)

    if request.method == "POST":
        form = CompetitionForm(request.POST, instance=competition)
        if form.is_valid():
            with transaction.atomic():
                form.save()
                log_action(request, "competition.update", competition)
            messages.success(request, "比赛信息已更新。")
            return redirect("competitions:detail", pk=competition.pk)
        messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = CompetitionForm(instance=competition)

    return render(request, "competitions/competition_form.html", {
        "form": form, "competition": competition,
        "page_title": "修改比赛", "is_create": False,
    })


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------

@login_required_view
def competition_detail(request, pk):
    competition = get_object_or_404(
        Competition.objects.select_related("created_by", "archived_by"), pk=pk,
    )

    if not can_view_competition(request.user, competition):
        # 草稿对非管理员不可见：按"不存在"处理，避免泄露草稿的存在
        raise PermissionDenied("无权查看该比赛。")

    can_manage = can_manage_competition(request.user, competition)
    tasks = (
        competition.tasks
        .select_related("work_type", "template", "created_by")
        .annotate(active_assignment_count=Count(
            "assignments", filter=Q(assignments__is_active=True), distinct=True))
        .order_by("id")
    )

    # 任务可见性：只有被分配到该任务的干事才能看到它。
    #
    # 为什么要在查询层过滤而不是模板里判断：
    #   模板过滤意味着"数据已经查出来了，只是没显示"，
    #   一旦有人改模板或加了导出，别人任务的内容就会泄露。
    #   这里直接从查询里排除，看不到的任务根本不出现在内存中。
    #
    # 管理员不受限（他要配置与检查全部任务）；被授权审核本场比赛的人
    # 也需要看到本场任务，否则无法判断提交是否符合要求。
    if not can_manage and not can_review_competition(request.user, competition):
        tasks = tasks.filter(
            assignments__member=request.user,
            assignments__is_active=True,
        ).distinct()

    context = {
        "competition": competition,
        "tasks": tasks,
        "can_manage": can_manage,
        "can_configure_tasks": can_manage and competition.can_modify_tasks,
        "can_manage_reviewers": can_manage_reviewers(request.user, competition),
        "reviewer_assignments": (
            reviewer_assignments_for(competition) if can_manage else []
        ),
        "archive_form": ArchiveCompetitionForm(competition=competition),
        "page_title": competition.name,
    }
    return render(request, "competitions/competition_detail.html", context)


# ---------------------------------------------------------------------------
# 状态流转：开始进行 / 归档
# ---------------------------------------------------------------------------

@admin_required
@require_POST
def competition_activate(request, pk):
    """草稿 → 进行中。只有进行中的比赛才能配置任务与分配人员。"""
    competition = get_object_or_404(Competition, pk=pk)
    if not can_manage_competition(request.user, competition):
        raise PermissionDenied("无权操作该比赛。")

    if competition.is_archived:
        messages.error(request, "已归档的比赛不能重新开始。")
        return redirect("competitions:detail", pk=competition.pk)
    if competition.is_active:
        messages.info(request, "该比赛已经是「进行中」状态。")
        return redirect("competitions:detail", pk=competition.pk)

    with transaction.atomic():
        competition.status = CompetitionStatus.ACTIVE
        competition.save(update_fields=["status", "updated_at"])
        log_action(request, "competition.activate", competition)

    messages.success(
        request,
        "比赛「%s」已进入进行中，现在可以配置任务和分配人员。" % competition.name,
    )
    return redirect("competitions:detail", pk=competition.pk)


@admin_required
@require_POST
def competition_archive(request, pk):
    """归档比赛。

    归档的影响：停止新增工作记录（不能再建任务/分配人员），
    但既有记录全部保留，后续阶段仍需按规则完成审核与修改。
    因此要求输入比赛名称二次确认。
    """
    competition = get_object_or_404(Competition, pk=pk)
    if not can_manage_competition(request.user, competition):
        raise PermissionDenied("无权归档该比赛。")

    if competition.is_archived:
        messages.info(request, "该比赛已经归档。")
        return redirect("competitions:detail", pk=competition.pk)

    form = ArchiveCompetitionForm(request.POST, competition=competition)
    if not form.is_valid():
        for error in form.errors.get("confirm_name", []):
            messages.error(request, error)
        return redirect("competitions:detail", pk=competition.pk)

    with transaction.atomic():
        changed = competition.archive(by_user=request.user)
        if changed:
            log_action(
                request, "competition.archive", competition,
                detail="原状态：%s" % CompetitionStatus.DRAFT.label
                if not competition.is_active else "",
            )

    messages.success(
        request,
        "比赛「%s」已归档。既有任务与分配记录全部保留，不再接收新的工作记录。"
        % competition.name,
    )
    return redirect("competitions:detail", pk=competition.pk)


# ---------------------------------------------------------------------------
# 审核人授权
# ---------------------------------------------------------------------------

@admin_required
@require_http_methods(["GET", "POST"])
def reviewer_manage(request, pk):
    """审核人授权管理页。"""
    from tasks.forms import ReviewerAssignmentForm

    competition = get_object_or_404(Competition, pk=pk)
    if not can_manage_reviewers(request.user, competition):
        raise PermissionDenied("无权管理该比赛的审核人授权。")

    if request.method == "POST":
        form = ReviewerAssignmentForm(request.POST, competition=competition)
        if form.is_valid():
            reviewers = form.cleaned_data["reviewers"]
            created, skipped = 0, 0
            with transaction.atomic():
                for user in reviewers:
                    # 用 get_or_create + 唯一约束兜底，避免并发重复授权
                    obj, is_new = ReviewerAssignment.objects.get_or_create(
                        competition=competition, reviewer=user,
                        defaults={"is_active": True, "granted_by": request.user},
                    )
                    if is_new:
                        created += 1
                        log_action(
                            request, "reviewer.grant", competition,
                            detail="授权审核人：%s" % user.get_full_name(),
                        )
                    elif not obj.is_active:
                        obj.is_active = True
                        obj.revoked_at = None
                        obj.granted_by = request.user
                        obj.save(update_fields=["is_active", "revoked_at", "granted_by"])
                        created += 1
                        log_action(
                            request, "reviewer.regrant", competition,
                            detail="重新授权审核人：%s" % user.get_full_name(),
                        )
                    else:
                        skipped += 1

            if created:
                messages.success(request, "已授权 %d 名审核人。" % created)
            if skipped:
                messages.info(request, "%d 名用户已在该比赛的审核人名单中，未重复授权。" % skipped)
            return redirect("competitions:reviewers", pk=competition.pk)
        messages.error(request, "请至少选择一名审核人。")
    else:
        form = ReviewerAssignmentForm(competition=competition)

    return render(request, "competitions/reviewer_manage.html", {
        "competition": competition,
        "form": form,
        "assignments": reviewer_assignments_for(competition),
        "page_title": "审核人授权 · %s" % competition.name,
    })


@admin_required
@require_POST
def reviewer_revoke(request, pk, assignment_id):
    """撤销审核人授权（不删除记录，保留历史）。"""
    competition = get_object_or_404(Competition, pk=pk)
    if not can_manage_reviewers(request.user, competition):
        raise PermissionDenied("无权管理该比赛的审核人授权。")

    assignment = get_object_or_404(
        ReviewerAssignment, pk=assignment_id, competition=competition,
    )

    with transaction.atomic():
        changed = assignment.revoke()
        if changed:
            log_action(
                request, "reviewer.revoke", competition,
                detail="撤销审核人：%s" % assignment.reviewer.get_full_name(),
            )

    if changed:
        messages.success(
            request, "已撤销 %s 的审核授权。" % assignment.reviewer.get_short_name(),
        )
    else:
        messages.info(request, "该授权已经是撤销状态。")
    return redirect("competitions:reviewers", pk=competition.pk)
