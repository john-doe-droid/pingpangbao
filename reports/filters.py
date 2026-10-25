# -*- coding: utf-8 -*-
"""报表的统一筛选层。

设计要点（这是 P4 最关键的一致性保障）
--------------------------------------
三张表（按人员 / 按工作类型 / 明细）必须**共用同一个筛选结果集**，
否则会出现「汇总数字和明细对不上」这种最难排查的问题。

因此：
    build_filtered_queryset(user, filters)  →  唯一的筛选入口
    parse_filters(params)                   →  唯一的解析入口（并做校验）

三张表都只调用这两个函数，不各自写查询条件。

关于「工作记录数量」的口径（P4 明确要求）
----------------------------------------
**按 Submission 数量计，不按版本数计。**
一条工作记录被退回后重新提交会产生新版本，但它仍只是**一条**记录。
因此所有计数一律用 `Count("id", distinct=True)` 作用在 Submission 上，
明细表也按记录（而非版本）出行，版本信息只作为附加列展示。

权限：
    可见范围完全复用 P3 的 `submissions.permissions.visible_submissions_for`，
    不另建一套判定 —— 管理员看全部，审核人看被授权比赛的，干事只看自己的。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from submissions.models import SubmissionStatus

# 统计维度可选值（页面下拉与此处保持一致）
STATUS_CHOICES_FOR_FILTER = [
    ("", "全部状态"),
    (SubmissionStatus.PENDING.value, "待审核"),
    (SubmissionStatus.NEEDS_REVISION.value, "退回修改"),
    (SubmissionStatus.APPROVED.value, "审核通过"),
]


@dataclass
class ReportFilters:
    """报表筛选条件。

    所有字段都是"未提供即为空"的语义，不做隐式默认 ——
    否则用户看到的结果会和自己的预期不符。
    """

    competition_id: int | None = None
    task_id: int | None = None
    work_type_id: int | None = None
    member_id: int | None = None
    status: str = ""
    date_from: date | None = None
    date_to: date | None = None
    include_overdue_only: bool = False

    def as_query(self) -> dict:
        """转回 URL 查询参数（用于页面上的"当前筛选"回显与导出链接）。"""
        data = {}
        if self.competition_id:
            data["competition"] = self.competition_id
        if self.task_id:
            data["task"] = self.task_id
        if self.work_type_id:
            data["work_type"] = self.work_type_id
        if self.member_id:
            data["member"] = self.member_id
        if self.status:
            data["status"] = self.status
        if self.date_from:
            data["date_from"] = self.date_from.isoformat()
        if self.date_to:
            data["date_to"] = self.date_to.isoformat()
        if self.include_overdue_only:
            data["overdue"] = "1"
        return data

    def describe(self) -> str:
        """人类可读的筛选说明（写进 Excel 的说明行，避免"这份表是什么口径"的疑问）。"""
        parts = []
        if self.competition_id:
            parts.append("指定比赛")
        if self.task_id:
            parts.append("指定任务")
        if self.work_type_id:
            parts.append("指定工作类型")
        if self.member_id:
            parts.append("指定成员")
        if self.status:
            label = dict(STATUS_CHOICES_FOR_FILTER).get(self.status, self.status)
            parts.append("状态=%s" % label)
        if self.date_from or self.date_to:
            parts.append("当前版本提交日期 %s ~ %s"
                         % (self.date_from.isoformat() if self.date_from else "不限",
                            self.date_to.isoformat() if self.date_to else "不限"))
        if self.include_overdue_only:
            parts.append("仅逾期提交")
        return "、".join(parts) if parts else "全部数据（未筛选）"


def _parse_int(value):
    """把查询参数解析成正整数；非法值返回 None（不静默当 0）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = int(text)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _parse_date(value):
    """解析 YYYY-MM-DD；格式不对返回 None（不抛异常给用户）。"""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_filters(params) -> tuple[ReportFilters, list[str]]:
    """从请求参数解析筛选条件，同时返回**被忽略的非法输入**提示。

    返回提示而不是静默忽略，是因为"我明明筛了却没生效"很难排查 ——
    页面会把这些提示显示出来。
    """
    warnings: list[str] = []

    def take_int(key):
        raw = params.get(key)
        if raw in (None, ""):
            return None
        parsed = _parse_int(raw)
        if parsed is None:
            warnings.append("「%s」取值无效（%r），已忽略该条件。" % (key, raw))
        return parsed

    def take_date(key):
        raw = params.get(key)
        if raw in (None, ""):
            return None
        parsed = _parse_date(raw)
        if parsed is None:
            warnings.append("「%s」日期格式无效（%r），应为 YYYY-MM-DD，已忽略。" % (key, raw))
        return parsed

    status = (params.get("status") or "").strip()
    valid_status = {value for value, _ in STATUS_CHOICES_FOR_FILTER if value}
    if status and status not in valid_status:
        warnings.append("「status」取值无效（%r），已忽略该条件。" % status)
        status = ""

    date_from = take_date("date_from")
    date_to = take_date("date_to")
    if date_from and date_to and date_from > date_to:
        warnings.append("开始日期晚于结束日期，已自动对调。")
        date_from, date_to = date_to, date_from

    filters = ReportFilters(
        competition_id=take_int("competition"),
        task_id=take_int("task"),
        work_type_id=take_int("work_type"),
        member_id=take_int("member"),
        status=status,
        date_from=date_from,
        date_to=date_to,
        include_overdue_only=(params.get("overdue") in ("1", "true", "on")),
    )
    return filters, warnings


def build_filtered_queryset(user, filters: ReportFilters):
    """三张表共用的唯一筛选入口。

    权限范围复用 P3 的 visible_submissions_for —— 不在报表里另写一套，
    避免"报表能看到的范围"和"页面能看到的范围"不一致。
    """
    from submissions.permissions import visible_submissions_for

    qs = visible_submissions_for(user)

    # 关联一次性取全，避免明细表逐行查库（N+1）
    qs = qs.select_related(
        "assignment",
        "assignment__member",
        "assignment__task",
        "assignment__task__competition",
        "assignment__task__work_type",
        "current_version",
    )

    if filters.competition_id:
        qs = qs.filter(assignment__task__competition_id=filters.competition_id)
    if filters.task_id:
        qs = qs.filter(assignment__task_id=filters.task_id)
    if filters.work_type_id:
        qs = qs.filter(assignment__task__work_type_id=filters.work_type_id)
    if filters.member_id:
        qs = qs.filter(assignment__member_id=filters.member_id)
    if filters.status:
        qs = qs.filter(status=filters.status)

    # 时间筛选落在**当前版本提交时间**上，并覆盖到当天整天。
    # 这样一条旧记录被退回后重新提交时，会进入最近一次修改所在的日期，
    # 也与页面明细中展示的“本版提交时间”保持一致。
    # 用户选"到 10-20"时，预期是包含 10-20 全天，而不是 00:00 整点。
    if filters.date_from:
        start = timezone.make_aware(
            datetime.combine(filters.date_from, time.min),
            timezone.get_current_timezone(),
        )
        qs = qs.filter(current_version__submitted_at__gte=start)
    if filters.date_to:
        end_date = filters.date_to + timedelta(days=1)
        end = timezone.make_aware(
            datetime.combine(end_date, time.min),
            timezone.get_current_timezone(),
        )
        qs = qs.filter(current_version__submitted_at__lt=end)

    if filters.include_overdue_only:
        # 逾期标记存在当前版本上（提交时刻冻结的判定）
        qs = qs.filter(current_version__is_overdue=True)

    return qs


# ---------------------------------------------------------------------------
# 统计口径说明（供页面与 Excel 展示，避免口径被误读）
# ---------------------------------------------------------------------------

COUNTING_NOTE = (
    "「工作记录数」按工作记录（Submission）计，一条记录无论被退回重提多少次都只计 1 条；"
    "「提交版本数」单独统计，用于反映修改次数。"
)
