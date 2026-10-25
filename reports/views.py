# -*- coding: utf-8 -*-
"""reports 视图：报表页面 + Excel 导出。

权限（P4 明确要求"一致的权限校验"）
----------------------------------
页面与导出**走同一个权限入口**：`statistics.build_report()` →
`filters.build_filtered_queryset()` → P3 的 `visible_submissions_for()`。

因此不存在"页面上看不到、导出却能拿到"的漏洞。
本模块**不做任何越权便利**，例如给管理员加"看全部"的旁路 —— 管理员本来就在
visible_submissions_for 里返回全部。

访问范围：页面与导出均仅社团管理员可用；干事在「我的提交」中查看个人记录。
"""
from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from core.permissions import admin_required
from . import excel, statistics
from .filters import (
    COUNTING_NOTE,
    STATUS_CHOICES_FOR_FILTER,
    build_filtered_queryset,
    parse_filters,
)


def _build_report(request):
    """解析筛选 + 生成报表数据（页面与导出共用，保证口径一致）。"""
    filters, warnings = parse_filters(request.GET)
    report = statistics.build_report(request.user, filters)
    report["generated_at"] = timezone.localtime()
    report["warnings"] = warnings
    return report, filters


def _filter_context(request, filters, warnings):
    """筛选表单所需的选项（比赛/任务/工作类型/成员）。"""
    from django.contrib.auth import get_user_model

    from tasks.models import CompetitionTask, WorkType

    # 只列出**当前用户有权看到的**比赛，避免下拉里出现别部门的比赛名
    from competitions.permissions import visible_competitions_for

    visible = visible_competitions_for(request.user)
    competitions = visible.order_by("-event_date", "-id")[:200]

    # 任务与成员限定在可见范围内，防止信息泄露
    visible_submissions = build_filtered_queryset(request.user, filters)
    task_ids = set(
        visible_submissions.values_list("assignment__task_id", flat=True)[:2000])
    member_ids = set(
        visible_submissions.values_list("assignment__member_id", flat=True)[:2000])

    tasks = (CompetitionTask.objects
             .filter(pk__in=task_ids)
             .select_related("competition", "work_type")
             .order_by("competition__event_date", "id")[:300])

    User = get_user_model()
    members = (User.objects
               .filter(pk__in=member_ids)
               .order_by("account")[:300])

    return {
        "competition_options": competitions,
        "task_options": tasks,
        "work_type_options": WorkType.objects.order_by("sort_order", "name"),
        "member_options": members,
        "status_options": STATUS_CHOICES_FOR_FILTER,
        "warnings": warnings,
        "filters": filters,
        "query_string": _query_string(filters),
    }


def _query_string(filters) -> str:
    """把筛选条件拼成查询串（导出链接与分页用）。"""
    from urllib.parse import urlencode

    return urlencode(filters.as_query())


@admin_required
def report_page(request):
    """报表页面（仅社团管理员）：筛选 + 三张表预览 + 导出入口。

    P6 规则变更：原本登录用户都能访问（干事只看得到自己的记录）。
    但实际使用时，干事看自己的记录去「我的工作 → 我的提交」就够了，
    报表页面对他们没有意义；而报表的筛选与汇总又天然是管理视角。
    因此收紧为仅管理员。这是**有意的权限收紧**，不是接口损坏：

      · 数据范围依旧由 visible_submissions_for() 决定（管理员=全部）；
      · 非管理员访问会得到 403，而不是静默跳到首页 ——
        静默跳转会让"为什么点不进去"变成一个说不清的问题。
    """
    report, filters = _build_report(request)
    context = _filter_context(request, filters, report["warnings"])
    context.update({
        "report": report,
        "summary": report["summary"],
        "by_member": report["by_member"],
        "by_work_type": report["by_work_type"],
        # 页面上明细只显示前若干条；完整明细请导出 Excel
        "details": report["details"][:200],
        "details_total": len(report["details"]),
        "counting_note": COUNTING_NOTE,
        "page_title": "统计与报表",
    })
    return render(request, "reports/report_page.html", context)


@admin_required
def export_xlsx(request):
    """导出 XLSX（三张工作表，仅社团管理员）。

    与页面共用 `_build_report` —— 这是"口径一致"的实现保证：
    页面看到什么，导出就是什么，不存在两套查询。
    """
    report, _filters = _build_report(request)

    data = excel.render_xlsx_bytes(report)
    filename = excel.suggested_filename(
        "CAUpingpangclub", at=timezone.localtime())

    # 文件名含中文：用 RFC 5987 的 filename* 让各浏览器都能正确还原
    from urllib.parse import quote

    response = HttpResponse(
        data,
        content_type=("application/vnd.openxmlformats-officedocument"
                      ".spreadsheetml.sheet"),
    )
    response["Content-Disposition"] = (
        "attachment; filename=\"report.xlsx\"; filename*=UTF-8''%s"
        % quote(filename)
    )
    response["Content-Length"] = str(len(data))
    # 报表含个人工作数据，禁止中间层缓存
    response["Cache-Control"] = "no-store"
    return response
