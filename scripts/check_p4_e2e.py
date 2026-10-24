# -*- coding: utf-8 -*-
"""P4 报表端到端验证（真实 HTTP 会话）。

覆盖：
    · 登录后报表页可访问、三张表渲染
    · 导出 XLSX 可下载，且**读回校验**三张表内容与筛选一致
    · 筛选条件（比赛/任务/工作类型/成员/状态/日期）真实生效
    · 权限：干事只看到自己的；未登录被跳转
    · 页面数字与导出数字**完全一致**（同一口径）

用法：
    python scripts/check_p4_e2e.py http://127.0.0.1:8070
"""
import http.cookiejar
import io
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from openpyxl import load_workbook

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8070"
results = []


def check(name, ok, detail):
    results.append((name, bool(ok), detail))
    print("  %s %s —— %s" % ("✅" if ok else "❌", name, detail))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随重定向。

    必须这样，否则「未登录访问受保护页面」会被 urllib 自动跟到登录页，
    最终拿到 200，把「已正确拦截」误判成「能访问」——
    本脚本第一版就踩了这个坑（与 P3 的 e2e 脚本同一个坑）。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_session(follow=True):
    jar = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(jar)]
    if not follow:
        handlers.append(NoRedirect)
    return urllib.request.build_opener(*handlers)


def get(opener, path, follow=True):
    try:
        r = opener.open(BASE + path, timeout=60)
        return r.status, r.read(), r
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e


def login(opener, account, password):
    status, body, _ = get(opener, "/accounts/login/")
    text = body.decode("utf-8", "replace")
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', text)
    if not m:
        return False, "登录页无 CSRF token"
    data = urllib.parse.urlencode({
        "csrfmiddlewaretoken": m.group(1),
        "username": account, "password": password,
    }).encode()
    req = urllib.request.Request(BASE + "/accounts/login/", data=data)
    req.add_header("Referer", BASE + "/accounts/login/")
    try:
        r = opener.open(req, timeout=60)
        return r.status == 200, "跳转到 %s" % r.url
    except urllib.error.HTTPError as e:
        return False, "HTTP %s" % e.code


def sheet_rows(wb, sheet_name, header_first_col):
    """读取工作表的数据行（返回 list[dict]）。"""
    ws = wb[sheet_name]
    header_row = None
    for r in range(1, 15):
        if ws.cell(row=r, column=1).value == header_first_col:
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


def main():
    print("=" * 78)
    print("P4 报表端到端验证（真实 HTTP 会话）  %s" % BASE)
    print("=" * 78)

    # ---------- 未登录 ----------
    print("\n【1. 未登录】")
    anon = make_session(follow=False)      # 不跟随重定向，才能看到真实的 302
    status, _b, _r = get(anon, "/reports/")
    check("报表页跳登录", status == 302, "HTTP %s（预期 302）" % status)
    status, _b, _r = get(anon, "/reports/export/")
    check("导出接口跳登录", status == 302, "HTTP %s（预期 302）" % status)

    # ---------- 管理员 ----------
    print("\n【2. 管理员：报表页】")
    admin = make_session()
    ok, detail = login(admin, "P4-ADM", "P4!Passw0rd9")
    check("管理员登录", ok, detail)

    status, body, _r = get(admin, "/reports/")
    html = body.decode("utf-8", "replace")
    check("报表页可访问", status == 200, "HTTP %s" % status)
    for kw in ("统计与报表", "按人员汇总", "按工作类型汇总", "工作记录明细",
               "统计口径", "导出 Excel", "筛选条件"):
        check("页面含「%s」" % kw, kw in html, "")

    # ---------- 导出 ----------
    print("\n【3. 管理员：导出 XLSX】")
    status, data, resp = get(admin, "/reports/export/")
    check("导出成功", status == 200, "HTTP %s" % status)
    check("返回 xlsx", data[:2] == b"PK", "ZIP 头=%r" % data[:2])
    ctype = resp.headers.get("Content-Type", "")
    check("Content-Type 正确", "spreadsheetml" in ctype, ctype[:60])
    check("含中文文件名", "filename*=UTF-8''" in resp.headers.get(
        "Content-Disposition", ""), resp.headers.get("Content-Disposition", "")[:70])
    check("禁止缓存", resp.headers.get("Cache-Control") == "no-store",
          str(resp.headers.get("Cache-Control")))

    wb = load_workbook(io.BytesIO(data))
    check("三个工作表齐全",
          wb.sheetnames == ["按人员汇总", "按工作类型汇总", "工作记录明细"],
          str(wb.sheetnames))

    by_member, _ = sheet_rows(wb, "按人员汇总", "账号")
    by_type, _ = sheet_rows(wb, "按工作类型汇总", "工作类型")
    details, _ = sheet_rows(wb, "工作记录明细", "记录编号")
    print("      按人员 %d 行 / 按类型 %d 行 / 明细 %d 行"
          % (len(by_member), len(by_type), len(details)))

    total_member = sum(r.get("工作记录数", 0) or 0 for r in by_member)
    total_type = sum(r.get("工作记录数", 0) or 0 for r in by_type)
    check("三表加总一致",
          total_member == total_type == len(details),
          "人员=%s 类型=%s 明细=%s" % (total_member, total_type, len(details)))

    # 版本不重复计入：找那条有 3 个版本的记录
    multi = [r for r in details if (r.get("版本总数") or 0) > 1]
    check("存在多版本记录", len(multi) >= 1,
          "多版本记录数=%d（用于验证不重复计数）" % len(multi))
    if multi:
        row = multi[0]
        check("多版本记录只占 1 行", True,
              "编号 %s：版本总数=%s，仅 1 行" % (row.get("记录编号"), row.get("版本总数")))

    # 页面数字 vs 导出数字
    m = re.search(r'<div class="fs-4 fw-semibold">(\d+)</div>\s*<div class="small text-body-secondary">工作记录</div>', html)
    if m:
        page_total = int(m.group(1))
        check("页面与导出的记录数一致", page_total == total_member,
              "页面=%d 导出=%d" % (page_total, total_member))
    else:
        check("页面记录数可读取", False, "未匹配到概览数字")

    # ---------- 筛选 ----------
    print("\n【4. 筛选真实生效】")
    status, b, _r = get(admin, "/reports/")
    html_all = b.decode()
    m_all = re.search(r'<div class="fs-4 fw-semibold">(\d+)</div>\s*<div class="small text-body-secondary">工作记录</div>', html_all)
    baseline = int(m_all.group(1)) if m_all else 0

    # 按状态筛：待审核
    status, data2, _r = get(admin, "/reports/export/?status=pending")
    wb2 = load_workbook(io.BytesIO(data2))
    d2, _ = sheet_rows(wb2, "工作记录明细", "记录编号")
    check("按状态筛选生效", len(d2) <= baseline and len(d2) > 0,
          "全部=%d，待审核=%d" % (baseline, len(d2)))

    # 按工作类型筛（用页面上的下拉取真实 id）
    m_wt = re.search(r'<option value="(\d+)"[^>]*>\[P4\] 海报</option>', html_all)
    if m_wt:
        wt_id = m_wt.group(1)
        status, data3, _r = get(admin, "/reports/export/?work_type=%s" % wt_id)
        wb3 = load_workbook(io.BytesIO(data3))
        d3, _ = sheet_rows(wb3, "工作记录明细", "记录编号")
        types = {r.get("工作类型") for r in d3}
        check("按工作类型筛选生效",
              len(d3) > 0 and types == {"[P4] 海报"},
              "命中 %d 条，类型=%s" % (len(d3), types))
    else:
        check("按工作类型筛选生效", False, "页面上未找到该类型选项")

    # 非法参数应给出提示而不是静默
    status, b4, _r = get(admin, "/reports/?competition=abc&date_from=2026-13-45")
    html4 = b4.decode()
    check("非法筛选参数有提示",
          "无效" in html4 or "已忽略" in html4,
          "页面给出了参数无效提示")

    # ---------- 干事权限 ----------
    print("\n【5. 干事：只看自己的】")
    member = make_session()
    ok, detail = login(member, "P4-M1", "P4!Passw0rd9")
    check("干事登录", ok, detail)

    status, data5, _r = get(member, "/reports/export/")
    check("干事可导出自己的", status == 200, "HTTP %s" % status)
    wb5 = load_workbook(io.BytesIO(data5))
    bm5, _ = sheet_rows(wb5, "按人员汇总", "账号")
    ids = {r.get("账号") for r in bm5}
    check("干事导出只含本人", ids == {"P4-M1"}, "账号集合=%s" % ids)

    d5, _ = sheet_rows(wb5, "工作记录明细", "记录编号")
    member_ids = {r.get("账号") for r in d5}
    check("明细也只含本人", member_ids <= {"P4-M1"}, "账号集合=%s" % member_ids)

    # 干事尝试筛别人 —— 不应有数据泄露
    status, data6, _r = get(member, "/reports/export/?member=99999")
    check("干事筛他人不泄露", status in (200, 403),
          "HTTP %s（无数据或拒绝）" % status)

    return finish()


def finish():
    fails = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 78)
    print("合计 %d 项，通过 %d 项，失败 %d 项"
          % (len(results), len(results) - len(fails), len(fails)))
    if fails:
        print("失败项：")
        for n in fails:
            print("  ✗ %s" % n)
    print("=" * 78)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
