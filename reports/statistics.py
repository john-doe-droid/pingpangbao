# -*- coding: utf-8 -*-
"""统计层：三个数据集，全部基于同一个筛选结果集。

三个数据集
----------
    by_member()      按人员汇总
    by_work_type()   按工作类型汇总
    details()        工作记录明细（按记录出行，不是按版本）

计数口径（P4 硬要求）
--------------------
「工作记录数」= **Submission 的数量**，一条记录被退回重提 N 次仍只算 1 条。
实现上：
    · 汇总用 `Count("id", distinct=True)` 作用在 Submission 上；
    · 版本数单独用 `Count("versions", distinct=True)` 统计，只作为参考列；
    · 明细表按记录出行，版本号只展示"当前是第几版"。

这样三种统计的加总天然一致：明细行数 == 按人员汇总的记录数合计
== 按工作类型汇总的记录数合计。**这一点有专门的测试守着。**
"""
from __future__ import annotations

from django.db.models import Count, Q

from submissions.models import SubmissionStatus

from .filters import build_filtered_queryset


def _base_aggregate(user, filters):
    """汇总用的基础查询集：附带各口径需要的聚合。"""
    return (
        build_filtered_queryset(user, filters)
        .annotate(
            # 注意作用对象是 Submission.id —— 保证不会因 join 版本表而放大
            total_records=Count("id", distinct=True),
            version_count=Count("versions", distinct=True),
        )
    )


def summarize(user, filters) -> dict:
    """整体概览（页面顶部与 Excel 说明行用）。

    返回记录数、版本数、各状态计数、逾期数 —— 全部基于同一筛选集。
    """
    qs = build_filtered_queryset(user, filters)

    # 各状态计数：一次查询里用条件聚合成多列，避免按状态查多次
    agg = qs.aggregate(
        total=Count("id", distinct=True),
        versions=Count("versions", distinct=True),
        pending=Count("id", filter=Q(status=SubmissionStatus.PENDING), distinct=True),
        needs_revision=Count(
            "id", filter=Q(status=SubmissionStatus.NEEDS_REVISION), distinct=True),
        approved=Count("id", filter=Q(status=SubmissionStatus.APPROVED), distinct=True),
        overdue=Count("id", filter=Q(current_version__is_overdue=True), distinct=True),
    )
    return {
        "total_records": agg["total"] or 0,
        "version_count": agg["versions"] or 0,
        "pending": agg["pending"] or 0,
        "needs_revision": agg["needs_revision"] or 0,
        "approved": agg["approved"] or 0,
        "overdue": agg["overdue"] or 0,
    }


def by_member(user, filters) -> list[dict]:
    """按人员汇总。

    每行：干事 + 学号 + 记录数 + 版本数 + 各状态数 + 逾期数 + 各类材料数量。
    """
    qs = (
        build_filtered_queryset(user, filters)
        .values(
            "assignment__member_id",
            "assignment__member__student_id",
            "assignment__member__real_name",
        )
        .annotate(
            total_records=Count("id", distinct=True),
            version_count=Count("versions", distinct=True),
            pending=Count("id", filter=Q(status=SubmissionStatus.PENDING), distinct=True),
            needs_revision=Count(
                "id", filter=Q(status=SubmissionStatus.NEEDS_REVISION), distinct=True),
            approved=Count(
                "id", filter=Q(status=SubmissionStatus.APPROVED), distinct=True),
            overdue=Count(
                "id", filter=Q(current_version__is_overdue=True), distinct=True),
            image_count=Count(
                "versions__attachments",
                filter=Q(versions__attachments__material_type="image",
                         versions__attachments__is_thumbnail=False),
                distinct=True,
            ),
            file_count=Count(
                "versions__attachments",
                filter=Q(versions__attachments__material_type="file"),
                distinct=True,
            ),
            video_count=Count(
                "versions__attachments",
                filter=Q(versions__attachments__material_type="video"),
                distinct=True,
            ),
            link_count=Count(
                "versions__attachments",
                filter=Q(versions__attachments__material_type="link"),
                distinct=True,
            ),
        )
        .order_by("assignment__member__student_id")
    )

    rows = []
    for item in qs:
        rows.append({
            "member_id": item["assignment__member_id"],
            "student_id": item["assignment__member__student_id"],
            "real_name": item["assignment__member__real_name"],
            "total_records": item["total_records"] or 0,
            "version_count": item["version_count"] or 0,
            "pending": item["pending"] or 0,
            "needs_revision": item["needs_revision"] or 0,
            "approved": item["approved"] or 0,
            "overdue": item["overdue"] or 0,
            "image_count": item["image_count"] or 0,
            "file_count": item["file_count"] or 0,
            "video_count": item["video_count"] or 0,
            "link_count": item["link_count"] or 0,
        })
    return rows


def by_work_type(user, filters) -> list[dict]:
    """按工作类型汇总。"""
    qs = (
        build_filtered_queryset(user, filters)
        .values(
            "assignment__task__work_type_id",
            "assignment__task__work_type__name",
        )
        .annotate(
            total_records=Count("id", distinct=True),
            version_count=Count("versions", distinct=True),
            pending=Count("id", filter=Q(status=SubmissionStatus.PENDING), distinct=True),
            needs_revision=Count(
                "id", filter=Q(status=SubmissionStatus.NEEDS_REVISION), distinct=True),
            approved=Count(
                "id", filter=Q(status=SubmissionStatus.APPROVED), distinct=True),
            overdue=Count(
                "id", filter=Q(current_version__is_overdue=True), distinct=True),
            member_count=Count("assignment__member_id", distinct=True),
            task_count=Count("assignment__task_id", distinct=True),
            competition_count=Count(
                "assignment__task__competition_id", distinct=True),
        )
        .order_by("assignment__task__work_type__name")
    )

    rows = []
    for item in qs:
        rows.append({
            "work_type_id": item["assignment__task__work_type_id"],
            "work_type_name": item["assignment__task__work_type__name"] or "（未分类）",
            "total_records": item["total_records"] or 0,
            "version_count": item["version_count"] or 0,
            "pending": item["pending"] or 0,
            "needs_revision": item["needs_revision"] or 0,
            "approved": item["approved"] or 0,
            "overdue": item["overdue"] or 0,
            "member_count": item["member_count"] or 0,
            "task_count": item["task_count"] or 0,
            "competition_count": item["competition_count"] or 0,
        })
    return rows


def details(user, filters) -> list[dict]:
    """工作记录明细。

    **按记录出行**（不是按版本）—— 一条记录一行，展示当前版本号、
    版本总数与最新审核结果。这样明细行数必然等于汇总的记录数。
    """
    qs = build_filtered_queryset(user, filters).order_by(
        "-created_at", "-id",
    )

    rows = []
    for submission in qs:
        version = submission.current_version or submission.latest_version()
        latest_review = (
            submission.review_logs
            .select_related("reviewer")
            .order_by("-acted_at", "-id")
            .first()
        )
        rows.append({
            "submission_id": submission.pk,
            "competition_name": submission.competition.name,
            "task_name": submission.task.name,
            "work_type_name": submission.task.work_type.name,
            "member_student_id": submission.assignment.member.student_id,
            "member_name": submission.assignment.member.real_name,
            "status": submission.status,
            "status_label": submission.get_status_display(),
            "created_at": submission.created_at,
            "current_version_no": version.version_no if version else None,
            "version_count": submission.versions.count(),
            "submitted_at": version.submitted_at if version else None,
            "effective_deadline": version.effective_deadline if version else None,
            "is_overdue": bool(version.is_overdue) if version else False,
            "latest_review_action": (
                latest_review.get_action_display() if latest_review else ""),
            "latest_review_comment": (latest_review.comment or "") if latest_review else "",
            "latest_reviewer": (
                latest_review.reviewer.get_short_name()
                if latest_review and latest_review.reviewer else ""),
            "latest_review_at": latest_review.acted_at if latest_review else None,
        })
    return rows


def build_report(user, filters) -> dict:
    """把三张表 + 概览打成一个包（页面与 Excel 共用同一份数据）。"""
    from .filters import COUNTING_NOTE

    return {
        "summary": summarize(user, filters),
        "by_member": by_member(user, filters),
        "by_work_type": by_work_type(user, filters),
        "details": details(user, filters),
        "filters": filters,
        "filter_description": filters.describe(),
        "counting_note": COUNTING_NOTE,
        "generated_at": None,   # 由调用方填当前时间（便于测试注入）
    }
