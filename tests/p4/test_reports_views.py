# -*- coding: utf-8 -*-
"""P4 页面、权限与导出接口测试。

重点：
    · 页面与导出的数字**完全一致**（同一份数据，不是两套查询）
    · 权限：干事只看自己的；未登录被拦；筛他人不泄露
    · 导出响应头正确（xlsx、中文文件名、禁止缓存）
"""
import io

import pytest
from django.urls import reverse
from openpyxl import load_workbook


def _sheet_rows(wb, sheet_name, first_col_header):
    ws = wb[sheet_name]
    header_row = None
    for r in range(1, 15):
        if ws.cell(row=r, column=1).value == first_col_header:
            header_row = r
            break
    if header_row is None:
        return [], None
    headers = [ws.cell(row=header_row, column=c).value
               for c in range(1, ws.max_column + 1)]
    rows = []
    for r in range(header_row + 1, ws.max_row + 1):
        if not ws.cell(row=r, column=1).value:
            continue
        rows.append({headers[c - 1]: ws.cell(row=r, column=c).value
                     for c in range(1, len(headers) + 1)})
    return rows, headers


# ===========================================================================
# 页面
# ===========================================================================

@pytest.mark.django_db
class TestReportPage:

    def test_requires_login(self, client):
        """未登录必须被拦（不能只靠隐藏入口）。"""
        resp = client.get(reverse("reports:page"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    def test_export_requires_login(self, client):
        resp = client.get(reverse("reports:export"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    def test_page_renders_three_tables(self, rich_dataset, report_admin, report_client):
        client = report_client.as_(report_admin)
        resp = client.get(reverse("reports:page"))
        assert resp.status_code == 200
        html = resp.content.decode()

        for keyword in ("按人员汇总", "按工作类型汇总", "工作记录明细",
                        "统计口径", "导出 Excel", "筛选条件"):
            assert keyword in html, "页面缺少「%s」" % keyword

    def test_page_shows_multiversion_correctly(self, rich_dataset, report_admin,
                                               report_client):
        """页面上多版本记录应显示"第 3 版 / 共 3 版"，但只占一行。"""
        client = report_client.as_(report_admin)
        html = client.get(reverse("reports:page")).content.decode()
        assert "第 3 版 / 共 3 版" in html

    def test_page_empty_state(self, db, report_admin, report_client):
        client = report_client.as_(report_admin)
        resp = client.get(reverse("reports:page"))
        assert resp.status_code == 200
        assert "没有数据" in resp.content.decode() or "没有工作记录" in resp.content.decode()

    def test_invalid_filter_warns_on_page(self, rich_dataset, report_admin,
                                          report_client):
        """非法筛选参数应给出提示，而不是静默忽略。"""
        client = report_client.as_(report_admin)
        html = client.get(reverse("reports:page"),
                          {"competition": "abc"}).content.decode()
        assert "无效" in html or "已忽略" in html


# ===========================================================================
# 数字一致性
# ===========================================================================

@pytest.mark.django_db
class TestPageExportConsistency:

    def test_page_number_equals_export_number(self, rich_dataset, report_admin,
                                              report_client):
        """页面上显示的记录数必须与导出的完全一致。"""
        import re

        client = report_client.as_(report_admin)

        html = client.get(reverse("reports:page")).content.decode()
        # UI 改版后统计卡片的 class 变了（pp-stat__num 取代 fs-4 fw-semibold）。
        # 这里不再依赖具体 class 串，而是按"数字 + 紧随其后的标签"这一结构匹配，
        # 断言强度不变（仍然必须从页面真实读到"工作记录"这一项的数字）。
        m = re.search(
            r'>\s*(\d+)\s*<[^>]*>\s*'
            r'(?:<[^>]*>\s*)*工作记录\s*<', html)
        assert m, "页面上应能读到记录数"
        page_total = int(m.group(1))

        data = client.get(reverse("reports:export")).content
        wb = load_workbook(io.BytesIO(data))
        rows, _ = _sheet_rows(wb, "工作记录明细", "记录编号")

        assert page_total == len(rows) == rich_dataset["expected_records"]

    def test_filtered_page_and_export_agree(self, rich_dataset, report_admin,
                                            report_client):
        """带筛选时两者仍要一致。"""
        client = report_client.as_(report_admin)
        params = {"work_type": rich_dataset["poster_type"].pk}

        data = client.get(reverse("reports:export"), params).content
        wb = load_workbook(io.BytesIO(data))
        details, _ = _sheet_rows(wb, "工作记录明细", "记录编号")
        by_member, _ = _sheet_rows(wb, "按人员汇总", "学号")

        assert len(details) == sum(r["工作记录数"] or 0 for r in by_member) == 2


# ===========================================================================
# 导出响应
# ===========================================================================

@pytest.mark.django_db
class TestExportResponse:

    def test_content_type_and_headers(self, rich_dataset, report_admin, report_client):
        client = report_client.as_(report_admin)
        resp = client.get(reverse("reports:export"))

        assert resp.status_code == 200
        assert "spreadsheetml" in resp["Content-Type"]
        assert "attachment" in resp["Content-Disposition"]
        assert "filename*=UTF-8''" in resp["Content-Disposition"], "中文文件名需 RFC 5987"
        assert resp["Cache-Control"] == "no-store", "含个人数据不应被缓存"
        assert int(resp["Content-Length"]) == len(resp.content)

    def test_export_three_sheets(self, rich_dataset, report_admin, report_client):
        client = report_client.as_(report_admin)
        data = client.get(reverse("reports:export")).content
        assert data[:2] == b"PK"
        wb = load_workbook(io.BytesIO(data))
        assert wb.sheetnames == ["按人员汇总", "按工作类型汇总", "工作记录明细"]

    def test_export_respects_all_filters(self, rich_dataset, report_admin,
                                         report_client):
        client = report_client.as_(report_admin)
        member = rich_dataset["members"][1]

        data = client.get(reverse("reports:export"),
                          {"member": member.pk}).content
        wb = load_workbook(io.BytesIO(data))
        details, _ = _sheet_rows(wb, "工作记录明细", "记录编号")
        assert len(details) == 1
        assert details[0]["学号"] == member.student_id


# ===========================================================================
# 权限
# ===========================================================================

@pytest.mark.django_db
class TestReportPermissions:
    """报表权限 —— P6 规则变更。

    原规则：登录即可访问报表，干事的可见范围由权限层收窄（只看自己）。
    新规则：报表**仅社团管理员**可访问（@admin_required）。

    变更原因（业务侧）：干事看自己的记录去「我的工作记录」就够，
    报表的筛选与汇总天然是管理视角；入口对干事只会造成困惑。

    因此原来这三条"干事能看到自己的记录"的断言前提失效，
    改写为验证**权限已收紧且不泄露任何数据** —— 断言强度没有降低：
    原来守的是"干事拿不到别人的数据"，现在守的是"干事一条都拿不到"。
    """

    def test_member_denied_report_page(self, rich_dataset, p3_member,
                                       report_client):
        """干事访问报表页面必须被拒绝（403），而不是"能打开但只看自己"。"""
        client = report_client.as_(p3_member)
        resp = client.get(reverse("reports:page"))
        assert resp.status_code == 403, (
            "报表已收紧为仅管理员，干事应得到 403，实际 %s" % resp.status_code)

    def test_member_denied_export(self, rich_dataset, p3_member, report_client):
        """干事导出 Excel 同样必须被拒绝，不能靠"页面藏起来"来防护。"""
        client = report_client.as_(p3_member)
        resp = client.get(reverse("reports:export"))
        assert resp.status_code == 403, (
            "导出接口必须与页面同样受限，实际 %s" % resp.status_code)

    def test_member_cannot_see_others_via_filter(self, rich_dataset, p3_member,
                                                 p3_member2, report_client):
        """干事带筛选参数也无法越权导出（参数不构成绕过权限的通道）。"""
        client = report_client.as_(p3_member)
        resp = client.get(reverse("reports:export"),
                          {"member": p3_member2.pk})
        assert resp.status_code == 403

    def test_member_cannot_see_others_competition(self, rich_dataset, p3_member,
                                                  p3_member2, report_client):
        """通过比赛参数同样不能绕过。"""
        client = report_client.as_(p3_member)
        resp = client.get(reverse("reports:export"),
                          {"competition": rich_dataset["competition"].pk})
        assert resp.status_code == 403

    def test_reviewer_denied_report(self, rich_dataset, p3_member, p3_admin,
                                    report_client):
        """被授权审核的人也不看报表 —— 审核与统计是两件事。"""
        client = report_client.as_(p3_member)
        assert client.get(reverse("reports:page")).status_code == 403

    def test_admin_still_sees_all(self, rich_dataset, p3_admin, p3_member,
                                  p3_member2, report_client):
        """管理员不受影响，仍能看到全部成员的数据。"""
        from reports.filters import ReportFilters
        from reports import statistics

        rows = statistics.by_member(p3_admin, ReportFilters())
        sids = {r["student_id"] for r in rows}
        assert p3_member.student_id in sids
        assert p3_member2.student_id in sids

        client = report_client.as_(p3_admin)
        assert client.get(reverse("reports:page")).status_code == 200
        assert client.get(reverse("reports:export")).status_code == 200


# ===========================================================================
# 筛选选项的范围
# ===========================================================================

@pytest.mark.django_db
class TestFilterOptionsScope:

    def test_competition_options_limited_to_visible(self, rich_dataset, p3_member,
                                                    report_client):
        """筛选下拉只应列出当前用户可见的比赛，避免泄露比赛名称。"""
        from django.contrib.auth import get_user_model

        from accounts.models import Role
        from competitions.models import Competition, CompetitionStatus

        # 另造一场该干事完全无关的草稿比赛
        outsider_user = get_user_model().objects.create_user(
            student_id="P4R-OUT", password="P4!Passw0rd9", real_name="无关人")
        outsider_user.must_change_password = False
        outsider_user.save(update_fields=["must_change_password"])
        role, _ = Role.objects.get_or_create(code="club_admin", defaults={"name": "社团管理员"})
        outsider_user.roles.add(role)

        hidden = Competition.objects.create(
            name="与他无关的草稿赛", event_date=rich_dataset["competition"].event_date,
            default_deadline=rich_dataset["competition"].default_deadline,
            status=CompetitionStatus.DRAFT, created_by=outsider_user)

        client = report_client.as_(p3_member)
        html = client.get(reverse("reports:page")).content.decode()
        assert hidden.name not in html, "草稿比赛不应出现在干事的筛选下拉里"
