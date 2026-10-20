# -*- coding: utf-8 -*-
"""P4 统计口径与筛选测试。

重点覆盖三条 P4 硬要求：
    1. **版本不重复计入工作记录数**（一条记录多次重提仍算 1 条）
    2. 三张表加总一致（按人员 = 按工作类型 = 明细）
    3. 筛选条件真实生效，且非法参数给出提示而非静默忽略
"""
import pytest
from django.utils import timezone
from datetime import timedelta

from reports import statistics
from reports.filters import ReportFilters, build_filtered_queryset, parse_filters
from submissions.models import SubmissionStatus


# ===========================================================================
# 核心口径：版本不重复计入
# ===========================================================================

@pytest.mark.django_db
class TestCountingRule:
    """P4 最关键的口径 —— 版本数不等于记录数。"""

    def test_multiversion_record_counted_once(self, rich_dataset, report_admin):
        """一条记录被退回重提 2 次 → 3 个版本，但只算 1 条记录。"""
        report = statistics.build_report(report_admin, ReportFilters())

        assert report["summary"]["total_records"] == rich_dataset["expected_records"]
        assert report["summary"]["version_count"] == rich_dataset["expected_versions"]

        # 该记录的版本数确实为 3，但只贡献 1 条记录
        poster = rich_dataset["m1_poster"]
        assert poster.versions.count() == 3

        # 逐条核对人数与版本数的关系
        rows = statistics.by_member(report_admin, ReportFilters())
        m1_sid = rich_dataset["members"][0].student_id
        m1_row = next(r for r in rows if r["student_id"] == m1_sid)
        assert m1_row["version_count"] > m1_row["total_records"], (
            "版本数应大于记录数，证明没有把版本当记录计数"
        )

    def test_details_one_row_per_record(self, rich_dataset, report_admin):
        """明细按**记录**出行：多版本记录只占 1 行。"""
        rows = statistics.details(report_admin, ReportFilters())
        assert len(rows) == rich_dataset["expected_records"]

        ids = [r["submission_id"] for r in rows]
        assert len(ids) == len(set(ids)), "明细不得出现重复记录行"

        multi = [r for r in rows if r["version_count"] > 1]
        assert len(multi) == 1, "只有那条海报记录是多版本"
        assert multi[0]["version_count"] == 3
        assert multi[0]["current_version_no"] == 3

    def test_totals_consistent_across_three_tables(self, rich_dataset, report_admin):
        """三张表加总必须一致 —— 这是"汇总对不上明细"的根本防线。"""
        report = statistics.build_report(report_admin, ReportFilters())

        by_member_total = sum(r["total_records"] for r in report["by_member"])
        by_type_total = sum(r["total_records"] for r in report["by_work_type"])
        detail_rows = len(report["details"])
        overview = report["summary"]["total_records"]

        assert by_member_total == by_type_total == detail_rows == overview
        assert overview == rich_dataset["expected_records"]

    def test_version_count_not_used_as_record_count(self, rich_dataset, report_admin):
        """明确断言：记录数 != 版本数（防止实现被"简化"成用版本数计数）。"""
        report = statistics.build_report(report_admin, ReportFilters())
        assert report["summary"]["total_records"] != report["summary"]["version_count"]
        assert report["summary"]["version_count"] > report["summary"]["total_records"]

    def test_member_row_shows_both_numbers(self, rich_dataset, report_admin):
        """按人员汇总里，记录数与版本数分别列示，便于交叉核对。"""
        rows = statistics.by_member(report_admin, ReportFilters())
        m1_sid = rich_dataset["members"][0].student_id
        m1_row = next(r for r in rows if r["student_id"] == m1_sid)

        assert m1_row["total_records"] == 2       # 海报 1 条 + 推文 1 条
        assert m1_row["version_count"] == 4       # 海报 3 版 + 推文 1 版


# ===========================================================================
# 汇总维度
# ===========================================================================

@pytest.mark.django_db
class TestAggregation:

    def test_by_work_type_splits_correctly(self, rich_dataset, report_admin):
        rows = statistics.by_work_type(report_admin, ReportFilters())
        names = {r["work_type_name"] for r in rows}
        assert rich_dataset["poster_type"].name in names
        assert rich_dataset["article_type"].name in names

        poster = next(r for r in rows if r["work_type_name"] == rich_dataset["poster_type"].name)
        assert poster["total_records"] == 2      # m1 海报 + m2 海报
        assert poster["version_count"] == 4      # 3 + 1
        assert poster["member_count"] == 2

    def test_summary_status_breakdown(self, rich_dataset, report_admin):
        summary = statistics.summarize(report_admin, ReportFilters())
        assert summary["total_records"] == 3
        # 海报(3版,退回状态) + 推文(待审核) + 乙海报(已通过)
        assert summary["approved"] == 1
        assert summary["pending"] + summary["needs_revision"] + summary["approved"] == 3

    def test_empty_dataset_returns_zeros(self, db, report_admin):
        """没有任何数据时应返回 0，而不是报错。"""
        summary = statistics.summarize(report_admin, ReportFilters())
        assert summary["total_records"] == 0
        assert summary["version_count"] == 0
        assert statistics.by_member(report_admin, ReportFilters()) == []
        assert statistics.details(report_admin, ReportFilters()) == []


# ===========================================================================
# 筛选
# ===========================================================================

@pytest.mark.django_db
class TestFiltering:

    def test_filter_by_work_type(self, rich_dataset, report_admin):
        f = ReportFilters(work_type_id=rich_dataset["poster_type"].pk)
        report = statistics.build_report(report_admin, f)
        assert report["summary"]["total_records"] == 2
        rows = statistics.by_work_type(report_admin, f)
        assert len(rows) == 1
        assert rows[0]["work_type_name"] == rich_dataset["poster_type"].name

    def test_filter_by_member(self, rich_dataset, report_admin):
        m2 = rich_dataset["members"][1]
        f = ReportFilters(member_id=m2.pk)
        report = statistics.build_report(report_admin, f)
        assert report["summary"]["total_records"] == 1
        rows = statistics.details(report_admin, f)
        assert all(r["member_student_id"] == m2.student_id for r in rows)

    def test_filter_by_status(self, rich_dataset, report_admin):
        f = ReportFilters(status=SubmissionStatus.APPROVED.value)
        report = statistics.build_report(report_admin, f)
        assert report["summary"]["total_records"] == 1
        assert report["summary"]["approved"] == 1

    def test_filter_by_competition_and_task(self, rich_dataset, report_admin):
        f = ReportFilters(competition_id=rich_dataset["competition"].pk)
        assert statistics.summarize(report_admin, f)["total_records"] == 3

        f2 = ReportFilters(task_id=rich_dataset["article_task"].pk)
        assert statistics.summarize(report_admin, f2)["total_records"] == 1

    def test_date_filter_includes_whole_end_day(self, rich_dataset, report_admin):
        """日期筛选必须包含结束当天整天（用户预期），而不是截止到 00:00。"""
        today = timezone.localdate()
        f = ReportFilters(date_from=today, date_to=today)
        assert statistics.summarize(report_admin, f)["total_records"] == 3

        f2 = ReportFilters(date_from=today + timedelta(days=1))
        assert statistics.summarize(report_admin, f2)["total_records"] == 0

    def test_combined_filters(self, rich_dataset, report_admin):
        """多个条件应同时生效（AND 语义）。"""
        f = ReportFilters(
            work_type_id=rich_dataset["poster_type"].pk,
            member_id=rich_dataset["members"][0].pk,
        )
        assert statistics.summarize(report_admin, f)["total_records"] == 1

    def test_filters_shared_by_all_three_tables(self, rich_dataset, report_admin):
        """三张表必须共用同一筛选 —— 筛选后加总仍一致。"""
        f = ReportFilters(work_type_id=rich_dataset["poster_type"].pk)
        report = statistics.build_report(report_admin, f)
        assert (sum(r["total_records"] for r in report["by_member"])
                == sum(r["total_records"] for r in report["by_work_type"])
                == len(report["details"]))


# ===========================================================================
# 筛选参数解析
# ===========================================================================

@pytest.mark.django_db
class TestFilterParsing:

    def test_valid_params_parsed(self):
        f, warnings = parse_filters({
            "competition": "5", "task": "7", "work_type": "3",
            "member": "9", "status": "approved",
            "date_from": "2026-01-01", "date_to": "2026-12-31",
        })
        assert f.competition_id == 5 and f.task_id == 7
        assert f.work_type_id == 3 and f.member_id == 9
        assert f.status == "approved"
        assert f.date_from.isoformat() == "2026-01-01"
        assert warnings == []

    def test_invalid_int_warns_not_silently_ignored(self):
        """非法数字必须给出提示 —— 否则用户会以为筛选生效了。"""
        f, warnings = parse_filters({"competition": "abc"})
        assert f.competition_id is None
        assert len(warnings) == 1 and "competition" in warnings[0]

    def test_invalid_date_warns(self):
        f, warnings = parse_filters({"date_from": "2026-13-45"})
        assert f.date_from is None
        assert len(warnings) == 1 and "date_from" in warnings[0]

    def test_invalid_status_warns(self):
        f, warnings = parse_filters({"status": "not-a-status"})
        assert f.status == ""
        assert len(warnings) == 1

    def test_reversed_dates_swapped_with_warning(self):
        f, warnings = parse_filters({"date_from": "2026-12-31", "date_to": "2026-01-01"})
        assert f.date_from < f.date_to
        assert any("对调" in w for w in warnings)

    def test_zero_and_negative_rejected(self):
        f, warnings = parse_filters({"competition": "0", "member": "-3"})
        assert f.competition_id is None and f.member_id is None

    def test_query_roundtrip(self):
        """筛选条件能转回查询串（导出链接要用）。"""
        f = ReportFilters(competition_id=1, status="pending",
                          date_from=timezone.localdate())
        q = f.as_query()
        assert q["competition"] == 1 and q["status"] == "pending"
        assert "date_from" in q

    def test_description_mentions_conditions(self):
        f = ReportFilters(status="approved", include_overdue_only=True)
        text = f.describe()
        assert "审核通过" in text and "逾期" in text

    def test_description_when_empty(self):
        assert "全部数据" in ReportFilters().describe()


# ===========================================================================
# Excel 生成
# ===========================================================================

@pytest.mark.django_db
class TestExcelExport:

    def _report(self, user, filters=None):
        from datetime import datetime
        report = statistics.build_report(user, filters or ReportFilters())
        report["generated_at"] = datetime(2026, 10, 20, 12, 0)
        return report

    def test_workbook_has_three_sheets(self, rich_dataset, report_admin):
        import io

        from openpyxl import load_workbook

        from reports import excel

        data = excel.render_xlsx_bytes(self._report(report_admin))
        assert data[:2] == b"PK"
        wb = load_workbook(io.BytesIO(data))
        assert wb.sheetnames == ["按人员汇总", "按工作类型汇总", "工作记录明细"]

    def test_export_numbers_match_statistics(self, rich_dataset, report_admin):
        """导出内容必须与统计结果一致（不能是两套口径）。"""
        import io

        from openpyxl import load_workbook

        from reports import excel

        report = self._report(report_admin)
        wb = load_workbook(io.BytesIO(excel.render_xlsx_bytes(report)))

        ws = wb["工作记录明细"]
        header_row = next(r for r in range(1, 15)
                          if ws.cell(row=r, column=1).value == "记录编号")
        rows = sum(1 for r in range(header_row + 1, ws.max_row + 1)
                   if ws.cell(row=r, column=1).value)
        assert rows == len(report["details"]) == 3

    def test_multiversion_row_appears_once(self, rich_dataset, report_admin):
        """多版本记录在明细里**只出现一次**（关键口径的导出侧验证）。"""
        import io

        from openpyxl import load_workbook

        from reports import excel

        target_id = rich_dataset["m1_poster"].pk
        wb = load_workbook(io.BytesIO(excel.render_xlsx_bytes(self._report(report_admin))))
        ws = wb["工作记录明细"]
        header_row = next(r for r in range(1, 15)
                          if ws.cell(row=r, column=1).value == "记录编号")

        hits = [r for r in range(header_row + 1, ws.max_row + 1)
                if ws.cell(row=r, column=1).value == target_id]
        assert len(hits) == 1, "多版本记录在明细中不得重复出现"

        # 该行的版本总数应为 3
        version_col = None
        for c in range(1, ws.max_column + 1):
            if ws.cell(row=header_row, column=c).value == "版本总数":
                version_col = c
                break
        assert ws.cell(row=hits[0], column=version_col).value == 3

    def test_datetime_written_without_timezone(self, rich_dataset, report_admin):
        """openpyxl 拒绝带时区的 datetime —— 必须转换后再写。"""
        import io

        from openpyxl import load_workbook

        from reports import excel

        wb = load_workbook(io.BytesIO(excel.render_xlsx_bytes(self._report(report_admin))))
        ws = wb["工作记录明细"]
        header_row = next(r for r in range(1, 15)
                          if ws.cell(row=r, column=1).value == "记录编号")

        found = False
        for r in range(header_row + 1, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                value = ws.cell(row=r, column=c).value
                if hasattr(value, "tzinfo"):
                    assert value.tzinfo is None, "写入的 datetime 不得带时区"
                    found = True
        assert found, "应至少有一个日期时间单元格"

    def test_title_block_contains_scope_note(self, rich_dataset, report_admin):
        """表头说明必须写明统计口径，避免被误读。"""
        import io

        from openpyxl import load_workbook

        from reports import excel

        wb = load_workbook(io.BytesIO(excel.render_xlsx_bytes(self._report(report_admin))))
        ws = wb["按人员汇总"]
        text = " ".join(str(ws.cell(row=r, column=1).value or "") for r in range(1, 7))
        assert "统计口径" in text
        assert "工作记录数" in text

    def test_empty_dataset_still_valid(self, db, report_admin):
        """没有数据时也要产出合法文件（而不是崩溃）。"""
        import io

        from openpyxl import load_workbook

        from reports import excel

        data = excel.render_xlsx_bytes(self._report(report_admin))
        wb = load_workbook(io.BytesIO(data))
        assert len(wb.sheetnames) == 3

    def test_filename_has_timestamp(self):
        from datetime import datetime

        from reports import excel

        name = excel.suggested_filename(at=datetime(2026, 10, 20, 9, 30))
        assert name.endswith(".xlsx") and "20261020-0930" in name
