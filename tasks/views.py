"""
tasks 视图：工作类型、模板、比赛任务、人员分配，以及干事端「我的任务」。

安全要点：
- 写操作全部 POST + CSRF；
- 每个管理视图都检查管理员角色，干事端只查本人的分配记录（查询层面隔离）；
- 归档比赛下的写操作一律拒绝（不依赖前端是否显示按钮）。
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from competitions.models import Competition
from core.audit import log_action
from core.permissions import admin_required, login_required_view, user_is_admin

from .forms import (
    CompetitionTaskForm,
    TaskAssignmentForm,
    TaskEditForm,
    TaskTemplateForm,
    WorkTypeForm,
)
from .models import (
    CompetitionTask,
    TaskAssignment,
    TaskTemplate,
    WorkType,
)
from .permissions import (
    assignments_for_member,
    can_assign_task,
    can_configure_tasks,
)


# ===========================================================================
# 工作类型
# ===========================================================================

@admin_required
def worktype_list(request):
    """工作类型列表（仅管理员可进入和维护）。"""
    worktypes = WorkType.objects.annotate(
        template_count=Count("templates", distinct=True),
    ).order_by("sort_order", "name")

    return render(request, "tasks/worktype_list.html", {
        "worktypes": worktypes,
        "can_manage": user_is_admin(request.user),
        "page_title": "工作类型",
    })


@admin_required
@require_http_methods(["GET", "POST"])
def worktype_create(request):
    if request.method == "POST":
        form = WorkTypeForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                worktype = form.save(commit=False)
                worktype.created_by = request.user
                worktype.save()
                log_action(request, "worktype.create", worktype)
            messages.success(request, "工作类型「%s」已创建。" % worktype.name)
            return redirect("tasks_admin:worktype_list")
        messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = WorkTypeForm()

    return render(request, "tasks/worktype_form.html", {
        "form": form, "page_title": "新建工作类型", "is_create": True,
    })


@admin_required
@require_http_methods(["GET", "POST"])
def worktype_update(request, pk):
    worktype = get_object_or_404(WorkType, pk=pk)

    if request.method == "POST":
        form = WorkTypeForm(request.POST, instance=worktype)
        if form.is_valid():
            with transaction.atomic():
                form.save()
                log_action(request, "worktype.update", worktype)
            messages.success(request, "工作类型已更新。")
            return redirect("tasks_admin:worktype_list")
        messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = WorkTypeForm(instance=worktype)

    return render(request, "tasks/worktype_form.html", {
        "form": form, "worktype": worktype,
        "page_title": "修改工作类型", "is_create": False,
    })


@admin_required
@require_POST
def worktype_toggle(request, pk):
    """启用 / 停用工作类型。

    停用而非删除：被任务或模板引用过的工作类型不允许删除，
    否则历史记录会失去分类。
    """
    worktype = get_object_or_404(WorkType, pk=pk)

    with transaction.atomic():
        if worktype.is_active:
            worktype.deactivate()
            action = "worktype.deactivate"
            msg = ("工作类型「%s」已停用。它不会再出现在新任务的选择列表中，"
                   "但已有任务与历史记录不受影响。" % worktype.name)
        else:
            worktype.activate()
            action = "worktype.activate"
            msg = "工作类型「%s」已重新启用。" % worktype.name
        log_action(request, action, worktype)

    messages.success(request, msg)
    return redirect("tasks_admin:worktype_list")


# ===========================================================================
# 任务模板
# ===========================================================================

@admin_required
def template_list(request):
    templates = (
        TaskTemplate.objects
        .select_related("work_type", "created_by")
        .annotate(used_count=Count("competition_tasks", distinct=True))
        .order_by("-is_active", "name")
    )
    active_filter = request.GET.get("active", "").strip()
    if active_filter == "1":
        templates = templates.filter(is_active=True)
    elif active_filter == "0":
        templates = templates.filter(is_active=False)

    return render(request, "tasks/template_list.html", {
        "templates": templates,
        "can_manage": user_is_admin(request.user),
        "current_active": active_filter,
        "page_title": "任务模板",
    })


@admin_required
@require_http_methods(["GET", "POST"])
def template_create(request):
    if not WorkType.objects.filter(is_active=True).exists():
        messages.error(request, "还没有启用的工作类型，请先创建一个工作类型。")
        return redirect("tasks_admin:worktype_create")

    if request.method == "POST":
        form = TaskTemplateForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                template = form.save(commit=False)
                template.created_by = request.user
                template.save()
                log_action(request, "template.create", template)
            messages.success(
                request,
                "任务模板「%s」已创建，材料要求：%s。"
                % (template.name, template.snapshot_summary()),
            )
            return redirect("tasks_admin:template_list")
        messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = TaskTemplateForm()

    return render(request, "tasks/template_form.html", {
        "form": form, "page_title": "新建任务模板", "is_create": True,
    })


@admin_required
@require_http_methods(["GET", "POST"])
def template_update(request, pk):
    template = get_object_or_404(TaskTemplate, pk=pk)

    if request.method == "POST":
        form = TaskTemplateForm(request.POST, instance=template)
        if form.is_valid():
            with transaction.atomic():
                form.save()
                log_action(request, "template.update", template)
            used = template.competition_tasks.count()
            if used:
                messages.warning(
                    request,
                    "模板已更新。注意：已有 %d 个比赛任务使用的是创建当时的规则快照，"
                    "本次修改**不会**改变它们的材料要求；如需生效请另行调整那些任务。"
                    % used,
                )
            else:
                messages.success(request, "任务模板已更新。")
            return redirect("tasks_admin:template_list")
        messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = TaskTemplateForm(instance=template)

    return render(request, "tasks/template_form.html", {
        "form": form, "template": template,
        "page_title": "修改任务模板", "is_create": False,
        "used_count": template.competition_tasks.count(),
    })


@admin_required
@require_POST
def template_toggle(request, pk):
    """启用 / 停用模板。停用后不能用于新任务，已创建的任务不受影响。"""
    template = get_object_or_404(TaskTemplate, pk=pk)

    with transaction.atomic():
        if template.is_active:
            template.deactivate()
            action, msg = "template.deactivate", (
                "模板「%s」已停用。它不会再出现在新建任务的可选列表中，"
                "已创建的比赛任务不受影响。" % template.name
            )
        else:
            template.activate()
            action, msg = "template.activate", "模板「%s」已重新启用。" % template.name
        log_action(request, action, template)

    messages.success(request, msg)
    return redirect("tasks_admin:template_list")


# ===========================================================================
# 比赛任务
# ===========================================================================

@admin_required
@require_http_methods(["GET", "POST"])
def task_create(request, competition_pk):
    """Create a task directly, with an optional template as a shortcut."""
    competition = get_object_or_404(Competition, pk=competition_pk)

    if not can_configure_tasks(request.user, competition):
        if competition.is_archived:
            messages.error(request, "已归档的比赛不能新增任务。")
            return redirect("competitions:detail", pk=competition.pk)
        if competition.is_draft:
            messages.error(request, "草稿状态的比赛不能新增任务，请先点击「开始进行」。")
            return redirect("competitions:detail", pk=competition.pk)
        raise PermissionDenied("无权为该比赛创建任务。")

    if not WorkType.objects.filter(is_active=True).exists():
        messages.error(request, "请先创建一个工作类型。")
        return redirect("tasks_admin:worktype_create")

    if request.method == "POST":
        form = CompetitionTaskForm(request.POST, competition=competition)
        if form.is_valid():
            with transaction.atomic():
                task = form.build_task(
                    competition=competition,
                    created_by=request.user,
                )
                task.save()
                template = form.cleaned_data.get("template")
                log_action(
                    request, "task.create", task,
                    detail="来源模板：%s；材料要求：%s"
                    % (template.name if template else "直接创建", task.snapshot_summary()),
                )
            messages.success(
                request,
                "任务「%s」已创建，材料要求：%s。截止时间：%s。"
                % (task.name, task.snapshot_summary(), task.deadline_source),
            )
            return redirect("competitions:detail", pk=competition.pk)
        messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = CompetitionTaskForm(competition=competition)

    template_payload = {}
    for template in form.fields["template"].queryset:
        template_payload[str(template.pk)] = {
            "name": template.name,
            "work_type": str(template.work_type_id),
            "description": template.description,
            "max_records": template.max_records_per_member,
            "text": (
                "required" if template.require_text
                else "optional" if template.allow_text else "none"
            ),
            "image": (
                "required" if template.require_image
                else "optional" if template.allow_image else "none"
            ),
            "file": (
                "required" if template.require_file
                else "optional" if template.allow_file else "none"
            ),
            "link": (
                "required" if template.require_link
                else "optional" if template.allow_link else "none"
            ),
        }

    return render(request, "tasks/task_form.html", {
        "form": form, "competition": competition,
        "template_payload": template_payload,
        "page_title": "为「%s」新建任务" % competition.name,
    })


@admin_required
def task_detail(request, pk):
    """任务详情（管理视角）。

    这是**管理端**页面（/manage/tasks/tasks/<pk>/），因此严格只对管理员开放：
    - 干事一律 403，即使该任务恰好分配给了他本人。

    为什么不给干事做"重定向到他自己的任务页"这类友好跳转：
      1. 权限边界要统一 —— 其它管理页对干事都是 403，只有这里 302 会让边界说不清；
      2. 要 fail-closed —— 若日后给本视图加了写操作副作用，"部分可用"会变成隐患；
      3. 避免跨命名空间耦合 —— 管理端视图不该 import 干事端权限并跳进干事端路由。
    干事看自己的任务走 /my/tasks/ 即可，那条路径有独立的对象级校验。
    """
    task = get_object_or_404(
        CompetitionTask.objects.select_related(
            "competition", "work_type", "template", "created_by"),
        pk=pk,
    )

    assignments = (
        task.assignments.filter(is_active=True)
        .select_related("member", "assigned_by")
        .order_by("member__account")
    )

    return render(request, "tasks/task_detail.html", {
        "task": task,
        "competition": task.competition,
        "assignments": assignments,
        "material_rows": task.snapshot_material_rows(),
        "can_assign": can_assign_task(request.user, task),
        "assignment_form": TaskAssignmentForm(task=task),
        "page_title": task.name,
    })


@admin_required
@require_http_methods(["GET", "POST"])
def task_update(request, pk):
    """修改任务。

    可以调整名称、说明与独立截止时间，但**不改材料规则快照** ——
    规则在创建时就已冻结，避免影响已被分配的干事。
    """
    task = get_object_or_404(
        CompetitionTask.objects.select_related("competition", "template"), pk=pk,
    )
    competition = task.competition

    if not can_configure_tasks(request.user, competition):
        if competition.is_archived:
            messages.error(request, "已归档的比赛下的任务不能修改。")
        else:
            messages.error(request, "当前比赛状态不允许修改任务。")
        return redirect("competitions:detail", pk=competition.pk)

    if request.method == "POST":
        form = TaskEditForm(request.POST, instance=task)
        if form.is_valid():
            with transaction.atomic():
                form.save()
                log_action(request, "task.update", task)
            messages.success(request, "任务已更新（材料要求保持创建时的快照不变）。")
            return redirect("competitions:detail", pk=competition.pk)
        messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = TaskEditForm(instance=task)

    return render(request, "tasks/task_edit_form.html", {
        "form": form, "task": task, "competition": competition,
        "page_title": "修改任务 · %s" % task.name,
    })



# ===========================================================================
# 人员分配
# ===========================================================================

@admin_required
@require_POST
def task_assign(request, pk):
    """为任务分配干事（支持一次多人）。"""
    task = get_object_or_404(
        CompetitionTask.objects.select_related("competition"), pk=pk,
    )

    if not can_assign_task(request.user, task):
        if task.competition.is_archived:
            messages.error(request, "已归档的比赛不能新增人员分配。")
            return redirect("competitions:detail", pk=task.competition.pk)
        raise PermissionDenied("无权为该任务分配人员。")

    form = TaskAssignmentForm(request.POST, task=task)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return redirect("tasks_admin:task_detail", pk=task.pk)

    members = form.cleaned_data["members"]
    created, duplicated = 0, 0
    with transaction.atomic():
        for member in members:
            try:
                # A nested atomic block creates a savepoint. A uniqueness
                # conflict then rolls back only this row instead of poisoning
                # the transaction for all other selected members.
                with transaction.atomic():
                    TaskAssignment.objects.create(
                        task=task, member=member, assigned_by=request.user,
                    )
                    created += 1
                    log_action(
                        request, "assignment.create", task,
                        detail="分配给：%s（%s）" % (
                            member.get_short_name(), member.account,
                        ),
                    )
            except IntegrityError:
                # 数据库部分唯一索引兜底：并发或绕过表单时不会产生重复分配
                duplicated += 1

    if created:
        messages.success(
            request, "已把任务「%s」分配给 %d 名干事。" % (task.name, created),
        )
    if duplicated:
        messages.warning(
            request, "有 %d 名干事此前已被分配过该任务，未重复添加。" % duplicated,
        )
    return redirect("tasks_admin:task_detail", pk=task.pk)


@admin_required
@require_POST
def assignment_deactivate(request, pk):
    """停用某条人员分配（不删除，保留历史）。"""
    assignment = get_object_or_404(
        TaskAssignment.objects.select_related("task", "task__competition", "member"),
        pk=pk,
    )
    if not can_assign_task(request.user, assignment.task):
        if assignment.task.competition.is_archived:
            messages.error(request, "已归档的比赛不能调整人员分配。")
            return redirect("competitions:detail", pk=assignment.task.competition.pk)
        raise PermissionDenied("无权限调整该分配。")

    with transaction.atomic():
        changed = assignment.deactivate()
        if changed:
            log_action(
                request, "assignment.deactivate", assignment.task,
                detail="停用分配：%s（%s）" % (
                    assignment.member.get_short_name(),
                    assignment.member.account,
                ),
            )

    if changed:
        messages.success(
            request,
            "已取消 %s 的该任务分配。该干事不再在「我的工作」中看到它，"
            "但历史记录已保留。" % assignment.member.get_short_name(),
        )
    else:
        messages.info(request, "该分配已经是停用状态。")
    return redirect("tasks_admin:task_detail", pk=assignment.task.pk)


# ===========================================================================
# 干事端：我的工作
# ===========================================================================

@login_required_view
def my_tasks(request):
    """我的工作待办列表。

    数据来源严格限定为 assignments_for_member(request.user)，
    从查询层面保证看不到别人的任务 —— 不依赖模板里的条件判断。
    """
    if user_is_admin(request.user) and not assignments_for_member(request.user).exists():
        messages.info(
            request,
            "您当前没有分配到的任务。作为管理员，您可以到「比赛管理」中配置任务与分配人员。",
        )

    assignments = list(assignments_for_member(request.user))
    # 局部导入：submissions 依赖 tasks，模块级互相导入会成环
    from django.urls import reverse

    from submissions.models import Submission, SubmissionStatus

    now_rows = []
    submissions = list(
        Submission.objects
        .filter(assignment__in=assignments)
        .select_related("current_version")
        .order_by("-created_at", "-id")
    )
    submissions_by_assignment = {}
    for submission in submissions:
        submissions_by_assignment.setdefault(submission.assignment_id, []).append(submission)

    for assignment in assignments:
        task = assignment.task
        assignment_submissions = submissions_by_assignment.get(assignment.id, [])
        used = len(assignment_submissions)
        limit = task.max_records_per_member
        remaining = max(limit - used, 0)
        revision = next(
            (
                item for item in assignment_submissions
                if item.status == SubmissionStatus.NEEDS_REVISION
            ),
            None,
        )
        latest = assignment_submissions[0] if assignment_submissions else None
        is_overdue = task.is_overdue()

        if revision is not None:
            status_kind, status_label = "danger", "待修改"
            action_label = "修改并重提"
            action_url = reverse("submissions:submit_revision", args=[revision.pk])
        elif remaining:
            status_kind = "danger" if is_overdue else "info"
            status_label = "已逾期，待提交" if is_overdue else "待提交"
            action_label = "提交成果"
            action_url = reverse("submissions:submit_new", args=[task.pk])
        elif latest is not None and latest.status == SubmissionStatus.PENDING:
            status_kind, status_label = "warning", "待审核"
            action_label = "查看记录"
            action_url = reverse("submissions:detail", args=[latest.pk])
        elif latest is not None and latest.status == SubmissionStatus.APPROVED:
            status_kind, status_label = "success", "已完成"
            action_label = "查看记录"
            action_url = reverse("submissions:detail", args=[latest.pk])
        else:
            status_kind, status_label = "neutral", "已提交"
            action_label = "查看记录"
            action_url = reverse("submissions:my_submissions")

        now_rows.append({
            "assignment": assignment,
            "task": task,
            "competition": task.competition,
            "is_overdue": is_overdue,
            "effective_deadline": task.effective_deadline,
            "used": used,
            "limit": limit,
            "remaining": remaining,
            "needs_action": bool(revision is not None or remaining),
            "status_kind": status_kind,
            "status_label": status_label,
            "action_label": action_label,
            "action_url": action_url,
        })

    return render(request, "tasks/my_tasks.html", {
        "rows": now_rows,
        "total": len(now_rows),
        "overdue_count": sum(
            1 for r in now_rows if r["is_overdue"] and r["needs_action"]
        ),
        "page_title": "我的工作",
    })


@login_required_view
def my_task_detail(request, task_pk):
    """我的工作任务详情。

    对象级校验：只有被分配了该任务的干事（或管理员）能打开。
    直接改 URL 里的 task_pk 会得到 403，而不是别人的任务内容。
    """
    task = get_object_or_404(
        CompetitionTask.objects.select_related(
            "competition", "work_type", "template"),
        pk=task_pk,
    )

    assignment = (
        assignments_for_member(request.user)
        .filter(task=task)
        .select_related("assigned_by")
        .first()
    )

    is_admin = user_is_admin(request.user)
    if assignment is None and not is_admin:
        raise PermissionDenied("该任务没有分配给您。")

    # 提交额度：详情页也要能直接提交，不用先回列表页（P6 简化）
    from submissions.models import Submission, SubmissionStatus

    used = limit = remaining = 0
    revision_submission = None
    can_submit_new = False
    if assignment is not None:
        assignment_submissions = list(
            Submission.objects.filter(assignment=assignment).order_by("-created_at", "-id")
        )
        used = len(assignment_submissions)
        limit = task.max_records_per_member
        remaining = max(limit - used, 0)
        revision_submission = next(
            (
                item for item in assignment_submissions
                if item.status == SubmissionStatus.NEEDS_REVISION
            ),
            None,
        )
        can_submit_new = bool(remaining and task.competition.is_active)

    return render(request, "tasks/my_task_detail.html", {
        "task": task,
        "competition": task.competition,
        "assignment": assignment,
        "is_overdue": task.is_overdue(),
        "effective_deadline": task.effective_deadline,
        "material_rows": task.snapshot_material_rows(),
        "is_admin_view": is_admin and assignment is None,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "revision_submission": revision_submission,
        "can_submit_new": can_submit_new,
        "page_title": task.name,
    })
