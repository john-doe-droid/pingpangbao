# -*- coding: utf-8 -*-
"""Excel 报表生成（openpyxl）。

三个工作表（P4 明确要求）
------------------------
    按人员汇总 | 按工作类型汇总 | 工作记录明细

一致性保障
----------
本模块**只消费 statistics.build_report() 的结果**，不自己查数据库 ——
因此页面上看到的数字与导出的 Excel 必然一致，不会出现两套口径。

格式约定：
    · 表头冻结 + 自动筛选，便于在 Excel 里继续筛
    · 列宽按内容估算，数字列右对齐、日期列统一格式
    · 首行上方写入筛选条件与计数口径说明（避免"这份表是什么口径"的疑问）
"""
from __future__ import annotations

import io
from datetime import date, datetime

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# --- 样式（集中定义，三张表统一）---
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(bold=True, size=13)
NOTE_FONT = Font(size=9, color="595959")
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center")

DATE_FMT = "yyyy-mm-dd"
DATETIME_FMT = "yyyy-mm-dd hh:mm"


def _excel_datetime(value):
    """把 Django 的带时区时间转成 Excel 能接受的形式。

    ⚠️ openpyxl 明确拒绝带 tzinfo 的 datetime：
        TypeError: Excel does not support timezones in datetimes.
    而本项目 USE_TZ=True，模型字段取出来**都是 aware datetime** ——
    直接写进单元格会让整个导出崩溃。

    正确做法：转成本地时区后去掉 tzinfo（Excel 里只存"墙上时间"）。
    这同时也符合用户预期：导出的表显示的是北京时间，而不是 UTC。
    """
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return value
    return value


def _write_value(cell, value):
    """写入单元格，并强制公式样式文本按普通字符串保存。"""
    cell.value = _excel_datetime(value)
    if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "-", "@")):
        cell.data_type = "s"


def _write_title_block(ws, title: str, report: dict, extra_rows: int = 0):
    """写标题 + 筛选条件 + 口径说明，返回表头所在行号。

    这些说明是刻意放在表里的：导出的文件常被转发给不看页面的人，
    没有口径说明很容易被误读（尤其"记录数 vs 版本数"）。
    """
    ws.cell(row=1, column=1, value=title).font = TITLE_FONT

    generated = report.get("generated_at")
    generated_text = generated.strftime("%Y-%m-%d %H:%M") if generated else "-"

    ws.cell(row=2, column=1,
            value="筛选条件：%s" % report.get("filter_description", "-")).font = NOTE_FONT
    ws.cell(row=3, column=1,
            value="生成时间：%s" % generated_text).font = NOTE_FONT
    ws.cell(row=4, column=1,
            value="统计口径：%s" % report.get("counting_note", "")).font = NOTE_FONT

    summary = report.get("summary") or {}
    ws.cell(row=5, column=1,
            value="合计：%d 条工作记录 / %d 个提交版本（待审核 %d、退回 %d、通过 %d、逾期 %d）"
                  % (summary.get("total_records", 0), summary.get("version_count", 0),
                     summary.get("pending", 0), summary.get("needs_revision", 0),
                     summary.get("approved", 0), summary.get("overdue", 0))
            ).font = NOTE_FONT

    return 7      # 表头写在第 7 行（留一行空行）


def _write_header(ws, row: int, headers: list[str]):
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=text)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = BORDER


def _autosize(ws, headers: list[str], rows: list[list], header_row: int,
              min_width: int = 8, max_width: int = 42):
    """按内容估算列宽（中文按 2 个字符宽度计）。

    注意：不写公式、不做合并，避免不同 Excel 版本打开时的兼容问题。
    """
    for idx, head in enumerate(headers, start=1):
        width = _display_width(str(head))
        for row in rows:
            if idx - 1 < len(row):
                width = max(width, _display_width(_cell_text(row[idx - 1])))
        ws.column_dimensions[get_column_letter(idx)].width = min(
            max(width + 2, min_width), max_width)


def _display_width(text: str) -> int:
    """估算显示宽度：中日韩字符算 2，其余算 1。"""
    total = 0
    for ch in text:
        total += 2 if ord(ch) > 0x2E80 else 1
    return total


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _apply_body_style(ws, first_row: int, last_row: int, col_count: int,
                      date_cols: set[int] | None = None,
                      datetime_cols: set[int] | None = None):
    date_cols = date_cols or set()
    datetime_cols = datetime_cols or set()
    for row in range(first_row, last_row + 1):
        for col in range(1, col_count + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = BORDER
            if col in date_cols and isinstance(cell.value, (date, datetime)):
                cell.number_format = DATE_FMT
                cell.alignment = CENTER
            elif col in datetime_cols and isinstance(cell.value, (date, datetime)):
                cell.number_format = DATETIME_FMT
                cell.alignment = CENTER
            elif isinstance(cell.value, (int, float)):
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.alignment = LEFT


# ---------------------------------------------------------------------------
# 三张表
# ---------------------------------------------------------------------------

def _sheet_by_member(wb, report: dict):
    ws = wb.create_sheet("按人员汇总")
    headers = ["账号", "姓名", "工作记录数", "提交版本数",
               "待审核", "退回修改", "审核通过", "逾期提交",
               "图片数", "文件数", "视频数", "链接数"]
    header_row = _write_title_block(ws, "按人员汇总", report)
    _write_header(ws, header_row, headers)

    rows = []
    for item in report["by_member"]:
        rows.append([
            item["account"], item["real_name"],
            item["total_records"], item["version_count"],
            item["pending"], item["needs_revision"], item["approved"], item["overdue"],
            item["image_count"], item["file_count"],
            item["video_count"], item["link_count"],
        ])

    for offset, row in enumerate(rows, start=header_row + 1):
        for col, value in enumerate(row, start=1):
            _write_value(ws.cell(row=offset, column=col), value)

    if rows:
        _apply_body_style(ws, header_row + 1, header_row + len(rows), len(headers))
        ws.auto_filter.ref = "A%d:%s%d" % (
            header_row, get_column_letter(len(headers)), header_row + len(rows))
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    _autosize(ws, headers, rows, header_row)
    return len(rows)


def _sheet_by_work_type(wb, report: dict):
    ws = wb.create_sheet("按工作类型汇总")
    headers = ["工作类型", "工作记录数", "提交版本数",
               "待审核", "退回修改", "审核通过", "逾期提交",
               "参与人数", "涉及任务数", "涉及比赛数"]
    header_row = _write_title_block(ws, "按工作类型汇总", report)
    _write_header(ws, header_row, headers)

    rows = []
    for item in report["by_work_type"]:
        rows.append([
            item["work_type_name"], item["total_records"], item["version_count"],
            item["pending"], item["needs_revision"], item["approved"], item["overdue"],
            item["member_count"], item["task_count"], item["competition_count"],
        ])

    for offset, row in enumerate(rows, start=header_row + 1):
        for col, value in enumerate(row, start=1):
            _write_value(ws.cell(row=offset, column=col), value)

    if rows:
        _apply_body_style(ws, header_row + 1, header_row + len(rows), len(headers))
        ws.auto_filter.ref = "A%d:%s%d" % (
            header_row, get_column_letter(len(headers)), header_row + len(rows))
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    _autosize(ws, headers, rows, header_row)
    return len(rows)


def _sheet_details(wb, report: dict):
    ws = wb.create_sheet("工作记录明细")
    headers = ["记录编号", "比赛", "任务", "工作类型",
               "账号", "姓名", "当前状态",
               "记录创建时间", "当前版本号", "版本总数",
               "本版提交时间", "生效截止时间", "是否逾期",
               "最近审核结果", "审核意见", "审核人", "审核时间"]
    header_row = _write_title_block(ws, "工作记录明细", report)
    _write_header(ws, header_row, headers)

    rows = []
    for item in report["details"]:
        rows.append([
            item["submission_id"], item["competition_name"], item["task_name"],
            item["work_type_name"], item["member_account"], item["member_name"],
            item["status_label"],
            item["created_at"], item["current_version_no"], item["version_count"],
            item["submitted_at"], item["effective_deadline"],
            "是" if item["is_overdue"] else "否",
            item["latest_review_action"], item["latest_review_comment"],
            item["latest_reviewer"], item["latest_review_at"],
        ])

    for offset, row in enumerate(rows, start=header_row + 1):
        for col, value in enumerate(row, start=1):
            _write_value(ws.cell(row=offset, column=col), value)

    if rows:
        _apply_body_style(
            ws, header_row + 1, header_row + len(rows), len(headers),
            date_cols={8, 11, 12}, datetime_cols={8, 11, 12, 17},
        )
        ws.auto_filter.ref = "A%d:%s%d" % (
            header_row, get_column_letter(len(headers)), header_row + len(rows))
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    _autosize(ws, headers, rows, header_row)
    return len(rows)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def build_workbook(report: dict) -> Workbook:
    """生成工作簿（三张表）。"""
    wb = Workbook()
    # 默认会建一个空 sheet，删掉它（三张表各自命名）
    wb.remove(wb.active)

    _sheet_by_member(wb, report)
    _sheet_by_work_type(wb, report)
    _sheet_details(wb, report)

    return wb


def render_xlsx_bytes(report: dict) -> bytes:
    """生成 xlsx 字节（供 HTTP 下载）。"""
    wb = build_workbook(report)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def suggested_filename(prefix: str = "CAUpingpangclub", at=None) -> str:
    """建议的下载文件名（带时间戳，避免多次导出互相覆盖）。"""
    stamp = (at or datetime.now()).strftime("%Y%m%d-%H%M")
    return "%s-%s.xlsx" % (prefix, stamp)
