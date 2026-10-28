# -*- coding: utf-8 -*-
"""submissions 视图：干事端提交、审核端审核、管理端查询。

所有写操作都是 POST + CSRF；所有对象访问都做**对象级**权限校验
（不信任 URL 里的主键，也不靠模板隐藏控制）。
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from core.permissions import (
    admin_required,
    login_required_view,
    user_is_admin,
    user_is_member,
)
from tasks.permissions import assignments_for_member

from . import orchestration, services
from .forms import ReopenForm, ReviewForm, SubmissionForm
from .models import Attachment, Submission, SubmissionStatus
from .permissions import (
    can_reopen_submission,
    can_review_submission,
    can_undo_rejection,
    can_view_attachment,
)


# ===========================================================================
# 干事端
# ===========================================================================

@login_required_view
def my_submissions(request):
    """我的提交列表。

    数据来源限定为本人（查询层面隔离），不依赖模板条件。
    """
    submissions = (
        Submission.objects
        .filter(assignment__member=request.user)
        .select_related(
            "assignment", "assignment__task", "assignment__task__competition",
            "assignment__task__work_type", "current_version",
        )
        .order_by("-created_at")
    )

    rows = []
    for sub in submissions:
        rows.append({
            "submission": sub,
            "task": sub.task,
            "competition": sub.competition,
            "version": sub.current_version or sub.latest_version(),
            "can_revise": bool(
                sub.can_be_revised
                and sub.assignment.is_active
                and user_is_member(request.user)
            ),
        })

    return render(request, "submissions/my_submissions.html", {
        "rows": rows,
        "page_title": "我的提交",
    })


@login_required_view
@require_http_methods(["GET", "POST"])
def submit_new(request, task_pk):
    """为某个已分配任务新建一条工作记录。"""
    assignment = get_object_or_404(
        assignments_for_member(request.user)
        .select_related("task", "task__competition", "task__work_type", "member"),
        task_id=task_pk,
    )
    task = assignment.task

    if request.method == "POST":
        # 表单只接收用户能决定的内容；提交人/状态/存储键全由后端决定
        form = SubmissionForm(request.POST, request.FILES, task=task)
        if form.is_valid():
            try:
                submission = orchestration.submit_new(
                    assignment_id=assignment.pk,
                    user=request.user,
                    text_content=form.cleaned_data.get("text_content", ""),
                    post_data=request.POST,
                    files=request.FILES,
                )
            except services.QuotaExceeded as exc:
                # 配额超限：显示具体数字，便于用户知道差多少
                messages.error(request, str(exc))
            except services.SubmissionError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "提交成功，状态为「待审核」。审核人处理后您会看到结果。"
                )
                return redirect("submissions:detail", pk=submission.pk)
        else:
            messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = SubmissionForm(task=task)

    return render(request, "submissions/submit_form.html", {
        "form": form,
        "assignment": assignment,
        "task": task,
        "competition": task.competition,
        "is_revision": False,
        "required_materials": form.required_materials(),
        "page_title": "提交工作成果",
    })


@login_required_view
@require_http_methods(["GET", "POST"])
def submit_revision(request, pk):
    """退回后修改并重新提交。

    不新建工作记录 —— 因此不额外占用新增次数（规则 6）。
    """
    submission = get_object_or_404(
        Submission.objects.select_related(
            "assignment", "assignment__member", "assignment__task",
            "assignment__task__competition", "assignment__task__work_type",
            "current_version",
        ),
        pk=pk,
    )

    if submission.assignment.member_id != request.user.id:
        raise PermissionDenied("这不是您的工作记录，无法修改。")
    if not submission.assignment.is_active or not user_is_member(request.user):
        raise PermissionDenied("该任务分配或干事权限已失效，不能继续修改。")
    if not submission.can_be_revised:
        messages.error(
            request,
            "当前状态为「%s」，只有「退回修改」的记录才能重新提交。"
            % submission.get_status_display(),
        )
        return redirect("submissions:detail", pk=submission.pk)

    task = submission.assignment.task
    last_version = submission.latest_version()

    if request.method == "POST":
        form = SubmissionForm(request.POST, request.FILES, task=task)
        if form.is_valid():
            try:
                version = orchestration.submit_revision(
                    submission_id=submission.pk,
                    user=request.user,
                    text_content=form.cleaned_data.get("text_content", ""),
                    post_data=request.POST,
                    files=request.FILES,
                )
            except services.QuotaExceeded as exc:
                messages.error(request, str(exc))
            except services.SubmissionError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "已重新提交（第 %d 版），状态回到「待审核」。"
                    "此前的版本与材料已完整保留。" % version.version_no,
                )
                return redirect("submissions:detail", pk=submission.pk)
        else:
            messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        # 预填上一版的文字说明，方便在原有基础上修改
        form = SubmissionForm(
            task=task,
            initial={"text_content": last_version.text_content if last_version else ""},
        )

    return render(request, "submissions/submit_form.html", {
        "form": form,
        "submission": submission,
        "task": task,
        "competition": task.competition,
        "is_revision": True,
        "last_version": last_version,
        "required_materials": form.required_materials(),
        "page_title": "修改并重新提交",
    })


@login_required_view
def submission_detail(request, pk):
    """工作记录详情（干事本人 / 管理员 / 授权审核人可见）。

    版本历史与退回理由对本人完整可见。
    """
    submission = get_object_or_404(
        Submission.objects.select_related(
            "assignment", "assignment__member", "assignment__task",
            "assignment__task__competition", "assignment__task__work_type",
            "current_version",
        ),
        pk=pk,
    )

    _assert_can_view_submission(request.user, submission)

    versions = (
        submission.versions
        .select_related("submitted_by")
        .prefetch_related("attachments", "review_logs")
        .order_by("-version_no")
    )
    review_logs = (
        submission.review_logs
        .select_related("reviewer", "version")
        .order_by("-acted_at")
    )

    is_owner = submission.assignment.member_id == request.user.id
    can_revise = bool(
        is_owner
        and submission.can_be_revised
        and submission.assignment.is_active
        and user_is_member(request.user)
    )

    return render(request, "submissions/submission_detail.html", {
        "submission": submission,
        "competition": submission.competition,
        "versions": versions,
        "review_logs": review_logs,
        "is_owner": is_owner,
        "can_revise": can_revise,
        "can_review": can_review_submission(request.user, submission),
        "can_reopen": can_reopen_submission(request.user, submission),
        "can_undo_rejection": can_undo_rejection(request.user, submission),
        "latest": submission.latest_version(),
        "page_title": "工作记录详情",
    })


def _assert_can_view_submission(user, submission):
    """对象级查看校验：本人 / 管理员 / 该比赛授权审核人。"""
    if submission.assignment.member_id == user.id:
        return
    if user_is_admin(user):
        return
    from competitions.permissions import can_review_competition

    if can_review_competition(user, submission.competition):
        return
    raise PermissionDenied("您没有权限查看这条工作记录。")


# ===========================================================================
# 附件下载跳转（生成短时链接）
# ===========================================================================

@login_required_view
def attachment_download(request, pk):
    """为某份附件生成短时下载链接并跳转。

    页面里**不出现**永久公开地址：这里每次都现场签发一个带有效期的链接。
    """
    attachment = get_object_or_404(
        Attachment.objects.select_related(
            "version", "version__submission",
            "version__submission__assignment",
            "version__submission__assignment__task",
            "version__submission__assignment__task__competition",
            "version__submission__assignment__member",
        ),
        pk=pk,
    )
    if not can_view_attachment(request.user, attachment):
        raise PermissionDenied("您没有权限访问该附件。")

    if attachment.is_link:
        # 链接类材料直接跳到外部地址（服务端不代访问）
        return redirect(attachment.external_url)

    from core.storage import get_storage

    store = get_storage()
    url = store.presigned_url(attachment.storage_key,
                              download_name=attachment.original_name)
    return redirect(url)


# ===========================================================================
# 审核端
# ===========================================================================

@login_required_view
def review_queue(request):
    """待我审核的记录（仅限我被授权的比赛）。"""
    from competitions.permissions import competitions_reviewable_by

    competitions = competitions_reviewable_by(request.user)
    submissions = (
        Submission.objects
        .filter(assignment__task__competition__in=competitions)
        .exclude(assignment__member=request.user)      # 不能审自己
        .select_related(
            "assignment", "assignment__member", "assignment__task",
            "assignment__task__competition", "assignment__task__work_type",
            "current_version",
        )
        .order_by("status", "-created_at")
    )

    pending = [s for s in submissions if s.status == SubmissionStatus.PENDING]
    others = [s for s in submissions if s.status != SubmissionStatus.PENDING]
    for item in others:
        item.can_undo_rejection = can_undo_rejection(request.user, item)

    return render(request, "submissions/review_queue.html", {
        "pending": pending,
        "others": others,
        "competitions": competitions,
        "page_title": "待审核记录",
    })


@login_required_view
@require_http_methods(["GET", "POST"])
def review_submission(request, pk):
    """审核一条记录：通过或退回。

    并发保护：服务层在事务内对 Submission 加行级锁并复核状态，
    因此两人同时点「通过」不会产生两个有效结果 ——
    后到的那个会看到状态已变，收到明确提示而不是静默覆盖。
    """
    submission = get_object_or_404(
        Submission.objects.select_related(
            "assignment", "assignment__member", "assignment__task",
            "assignment__task__competition",
        ),
        pk=pk,
    )

    if not can_review_submission(request.user, submission):
        raise PermissionDenied("您没有该比赛的审核权限，或不能审核自己提交的记录。")

    if request.method == "POST":
        form = ReviewForm(request.POST)
        if form.is_valid():
            action = form.cleaned_data["action"]
            comment = form.cleaned_data.get("comment", "")
            try:
                with transaction.atomic():
                    locked = services.lock_submission(submission.pk)
                    services.assert_review_permission(request.user, locked)
                    if action == "approve":
                        services.approve_submission(
                            submission=locked, reviewer=request.user, comment=comment,
                        )
                    else:
                        services.reject_submission(
                            submission=locked, reviewer=request.user, comment=comment,
                        )
            except services.SubmissionError as exc:
                # 并发冲突或状态不符 —— 明确提示，不静默
                messages.error(request, str(exc))
                return redirect("submissions:review_detail", pk=submission.pk)
            else:
                messages.success(
                    request,
                    "已%s。" % ("通过" if action == "approve" else "退回修改"),
                )
                return redirect("submissions:review_queue")
        else:
            messages.error(request, "表单填写有误，请检查下方提示。")
    else:
        form = ReviewForm()

    versions = (
        submission.versions
        .prefetch_related("attachments")
        .order_by("-version_no")
    )

    return render(request, "submissions/review_detail.html", {
        "submission": submission,
        "form": form,
        "versions": versions,
        "latest": submission.latest_version(),
        "review_logs": submission.review_logs.select_related("reviewer").order_by("-acted_at"),
        "can_undo_rejection": can_undo_rejection(request.user, submission),
        "page_title": "审核工作记录",
    })


@login_required_view
@require_POST
def undo_rejection(request, pk):
    """Undo a mistaken reject while the owner has not resubmitted."""
    submission = get_object_or_404(
        Submission.objects.select_related(
            "assignment", "assignment__member", "assignment__task",
            "assignment__task__competition", "current_version",
        ),
        pk=pk,
    )
    if not can_undo_rejection(request.user, submission):
        raise PermissionDenied("只有原退回审核人可以撤销本次退回。")

    try:
        with transaction.atomic():
            locked = services.lock_submission(submission.pk)
            services.assert_review_permission(request.user, locked)
            services.undo_rejection(submission=locked, reviewer=request.user)
    except services.SubmissionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "已撤销退回，这条记录恢复到「待审核」。")
    return redirect("submissions:review_detail", pk=submission.pk)


@login_required_view
@require_http_methods(["GET", "POST"])
def reopen_submission(request, pk):
    """对已通过的记录发起重新审核（需权限 + 必填原因）。

    原通过记录与完整历史都保留 —— 重新审核只是追加一条 ReviewLog
    并把状态改回待审核，不删除任何既有记录。
    """
    submission = get_object_or_404(
        Submission.objects.select_related(
            "assignment", "assignment__member", "assignment__task",
            "assignment__task__competition",
        ),
        pk=pk,
    )

    if not can_reopen_submission(request.user, submission):
        raise PermissionDenied(
            "只有该比赛的授权审核人才能对已通过记录发起重新审核。"
        )

    if request.method == "POST":
        form = ReopenForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    locked = services.lock_submission(submission.pk)
                    services.assert_review_permission(request.user, locked)
                    services.reopen_submission(
                        submission=locked,
                        reviewer=request.user,
                        reason=form.cleaned_data["reopen_reason"],
                        comment=form.cleaned_data.get("comment", ""),
                    )
            except services.SubmissionError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "已发起重新审核，该记录回到「待审核」。原通过记录与全部历史已保留。",
                )
                return redirect("submissions:review_detail", pk=submission.pk)
        else:
            messages.error(request, "请填写重新审核原因。")
    else:
        form = ReopenForm()

    return render(request, "submissions/reopen_form.html", {
        "submission": submission,
        "form": form,
        "page_title": "发起重新审核",
    })


@login_required_view
def review_detail(request, pk):
    """审核视角的记录详情（含材料预览）。

    与干事端详情复用同一模板的只读部分，但入口分开，
    避免干事端出现审核按钮、审核端出现"修改重提"按钮。
    """
    submission = get_object_or_404(
        Submission.objects.select_related(
            "assignment", "assignment__member", "assignment__task",
            "assignment__task__competition", "assignment__task__work_type",
        ),
        pk=pk,
    )
    if not can_review_submission(request.user, submission):
        # 本人也可以查看自己的记录（走干事端入口）
        if submission.assignment.member_id == request.user.id:
            return redirect("submissions:detail", pk=submission.pk)
        raise PermissionDenied("您没有权限审核这条记录。")

    return redirect("submissions:review_submission", pk=submission.pk)


# ===========================================================================
# 管理端
# ===========================================================================

@admin_required
def manage_submissions(request):
    """管理端：记录查询与审核历史查询。

    支持按比赛、状态过滤；只读用途为主。
    """
    submissions = (
        Submission.objects
        .select_related(
            "assignment", "assignment__member", "assignment__task",
            "assignment__task__competition", "current_version",
        )
        .order_by("-created_at")
    )

    competition_id = request.GET.get("competition", "").strip()
    status_filter = request.GET.get("status", "").strip()

    if competition_id.isdigit():
        submissions = submissions.filter(assignment__task__competition_id=int(competition_id))
    valid_status = {s.value for s in SubmissionStatus}
    if status_filter in valid_status:
        submissions = submissions.filter(status=status_filter)

    from competitions.models import Competition

    # 待清理对象数量：让"清理失败"这类问题在管理端可见
    from .models import OrphanObject

    return render(request, "submissions/manage_list.html", {
        "submissions": submissions[:300],
        "competitions": Competition.objects.order_by("-event_date"),
        "status_choices": SubmissionStatus.choices,
        "current_competition": competition_id,
        "current_status": status_filter,
        "orphan_count": OrphanObject.objects.filter(resolved=False).count(),
        "page_title": "全部记录",
    })
