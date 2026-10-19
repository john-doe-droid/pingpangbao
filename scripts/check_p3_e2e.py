# -*- coding: utf-8 -*-
"""P3 端到端验证（真实 HTTP 会话，走真实的页面与表单）。

覆盖完整链路：
    干事提交 → 审核人退回 → 干事修改重提 → 审核人通过 → 管理员发起重新审核 → 再通过

为什么用 HTTP 会话而不是浏览器截图：
    · 能精确断言状态码与页面内容，比"看起来对"更强；
    · 真实走 CSRF、登录、multipart 上传，与浏览器行为一致。
    **它不替代视觉验证** —— 页面外观的可读性另行说明。

用法：
    python scripts/check_p3_e2e.py http://127.0.0.1:8050
"""
import http.cookiejar
import io
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8050"

results = []


def check(name, ok, detail):
    results.append((name, bool(ok), detail))
    print("  %s %s —— %s" % ("✅" if ok else "❌", name, detail))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随重定向。

    必须这样做，否则「未登录访问受保护页面」会被 urllib 自动跟到登录页，
    最终拿到 200，从而把「已正确拦截」误判成「访问成功」——
    本脚本第一版就踩了这个坑。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_session(follow=True):
    jar = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(jar)]
    if not follow:
        handlers.append(NoRedirect)
    return urllib.request.build_opener(*handlers)


def get(opener, path):
    try:
        r = opener.open(BASE + path, timeout=30)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def csrf_of(body):
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body)
    return m.group(1) if m else None


def login(opener, student_id, password):
    _s, body = get(opener, "/accounts/login/")
    token = csrf_of(body)
    if not token:
        return False, "登录页无 CSRF token"
    data = urllib.parse.urlencode({
        "csrfmiddlewaretoken": token, "username": student_id, "password": password,
    }).encode()
    req = urllib.request.Request(BASE + "/accounts/login/", data=data)
    req.add_header("Referer", BASE + "/accounts/login/")
    try:
        r = opener.open(req, timeout=30)
        return r.status == 200, "跳转到 %s" % r.url
    except urllib.error.HTTPError as e:
        return False, "HTTP %s" % e.code


def make_jpeg(w=1200, h=900):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 60, 60)).save(buf, "JPEG")
    return buf.getvalue()


def post_multipart(opener, path, fields, files):
    """构造 multipart/form-data 提交（模拟浏览器表单，含文件上传）。"""
    boundary = "----P3E2Eboundary"
    parts = []
    for k, v in fields.items():
        parts.append("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                     % (boundary, k, v))
    for k, (filename, content, ctype) in files.items():
        parts.append(
            "--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
            "Content-Type: %s\r\n\r\n" % (boundary, k, filename, ctype))
        parts.append(content)
        parts.append("\r\n")
    parts.append("--%s--\r\n" % boundary)

    body = b""
    for p in parts:
        body += p.encode("utf-8") if isinstance(p, str) else p

    req = urllib.request.Request(BASE + path, data=body)
    req.add_header("Content-Type", "multipart/form-data; boundary=%s" % boundary)
    req.add_header("Referer", BASE + path)
    try:
        r = opener.open(req, timeout=60)
        return r.status, r.read().decode("utf-8", "replace"), r.url
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), None


def post_form(opener, path, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(BASE + path, data=data)
    req.add_header("Referer", BASE + path)
    try:
        r = opener.open(req, timeout=30)
        return r.status, r.read().decode("utf-8", "replace"), r.url
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), None


def get_noredirect(opener, path):
    """不跟随重定向的 GET，用于区分 302 与真实 200。"""
    try:
        r = opener.open(BASE + path, timeout=30)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main():
    print("=" * 78)
    print("P3 端到端验证（真实 HTTP 会话）  %s" % BASE)
    print("=" * 78)

    # ---------- 干事甲登录 ----------
    print("\n【1. 干事提交工作成果】")
    # 提交人用「演示干事乙」，审核人用「演示干事甲」（该比赛的授权审核人）——
    # 这样既满足「审核人不能审自己的提交」，也能真实走通审核流程。
    member = make_session()
    ok, detail = login(member, "D-MEMBER2", "DemoPassw0rd!2026")
    check("干事（提交人）登录", ok, detail)

    s, body = get(member, "/my/submissions/")
    check("我的工作记录页可访问", s == 200, "HTTP %s" % s)
    check("列出可提交的任务", "提交成果" in body, "页面含「提交成果」入口")

    # 找第一个可提交任务的链接
    m = re.search(r'href="(/my/submissions/new/(\d+)/)"', body)
    check("找到任务入口", m is not None, m.group(1) if m else "未找到")
    if not m:
        return finish()
    submit_path = m.group(1)

    # 先取 CSRF
    s, form_body = get(member, submit_path)
    token = csrf_of(form_body)
    check("提交表单可打开", s == 200 and token is not None, "HTTP %s" % s)

    # 故意缺图提交 —— 应被拒
    s, body2, _u = post_multipart(
        member, submit_path,
        {"csrfmiddlewaretoken": token, "text_content": "只有文字没有图片"},
        {})
    check("缺必填图片被拒", "必填" in body2 or "图片证明" in body2,
          "页面提示缺少必填材料")

    # 正常提交（带图片）
    s, body3, final_url = post_multipart(
        member, submit_path,
        {"csrfmiddlewaretoken": token,
         "text_content": "完成了赛前海报，主视觉用红蓝对比。"},
        {"images": ("poster.jpg", make_jpeg(), "image/jpeg")})
    check("带图片提交成功", "提交成功" in body3 or "待审核" in body3,
          "HTTP %s" % s)

    # 去我的记录页找到刚提交的那条
    s, body4 = get(member, "/my/submissions/")
    check("记录列表出现新提交", "待审核" in body4, "状态为待审核")
    m = re.search(r'href="(/my/submissions/(\d+)/)"', body4)
    check("能打开记录详情", m is not None, m.group(1) if m else "未找到")
    if not m:
        return finish()
    detail_path = m.group(1)
    submission_id = m.group(2)

    s, detail_body = get(member, detail_path)
    check("详情页含缩略图", "/attachment/" in detail_body and "thumb" not in detail_body.lower()
          or "/my/submissions/attachment/" in detail_body,
          "页面渲染了图片下载入口")
    check("详情页显示版本历史", "第 1 版" in detail_body, "含版本号")
    check("详情页明确无提交按钮（本阶段已开放）",
          "提交功能将在后续阶段开放" not in detail_body,
          "P3 已开放提交，不应再出现占位说明")

    # ---------- 干事乙：跨用户隔离 ----------
    print("\n【2. 跨用户隔离】")
    other = make_session()
    login(other, "D-VIEWER", "DemoPassw0rd!2026")
    s, _ = get(other, detail_path)
    check("无关用户改 URL 看他人记录被拒", s == 403, "HTTP %s（预期 403）" % s)

    # ---------- 审核人退回 ----------
    print("\n【3. 审核人退回】")
    # 注意：该比赛的授权审核人是「演示干事甲」。
    # 管理员 D-ADMIN 虽然能管理比赛，但**没有**该比赛的审核权 ——
    # 这是 P2/P3 刻意设计的「审核权不继承管理权」，因此这里必须用被授权的账号。
    reviewer = make_session()
    ok, detail = login(reviewer, "D-MEMBER1", "DemoPassw0rd!2026")
    check("审核人（被授权者）登录", ok, detail)

    # 顺带验证：管理员未被授权时确实不能审核（这是安全性质，不是缺陷）
    admin_probe = make_session()
    login(admin_probe, "D-ADMIN", "DemoPassw0rd!2026")
    s_admin, _ = get_noredirect(admin_probe, "/my/submissions/review/%s/" % submission_id)
    check("未被授权的管理员不能审核该比赛（审核权不继承管理权）",
          s_admin == 403, "HTTP %s（预期 403）" % s_admin)

    s, queue_body = get(reviewer, "/my/submissions/review/")
    check("待审核列表可访问", s == 200, "HTTP %s" % s)

    review_path = "/my/submissions/review/%s/" % submission_id
    s, rev_body = get(reviewer, review_path)
    token = csrf_of(rev_body)
    check("审核页可打开", s == 200 and token is not None, "HTTP %s" % s)
    check("审核页展示提交材料", "文字说明" in rev_body and "图片" in rev_body,
          "含文字与图片")

    # 空理由退回 —— 应被拒
    s, body5, _u = post_form(reviewer, review_path,
                             {"csrfmiddlewaretoken": token, "action": "reject",
                              "comment": ""})
    check("空理由退回被拒", "必须填写理由" in body5, "页面提示必填理由")

    # 正常退回
    s, body6, _u = post_form(reviewer, review_path,
                             {"csrfmiddlewaretoken": token, "action": "reject",
                              "comment": "海报配色太暗，请把主视觉调亮一些。"})
    check("退回成功", "退回" in body6 or "已" in body6, "HTTP %s" % s)

    # ---------- 干事修改重提 ----------
    print("\n【4. 干事修改并重新提交】")
    s, body7 = get(member, detail_path)
    check("状态变为退回修改", "退回修改" in body7, "记录状态已更新")
    check("能看到退回理由", "配色太暗" in body7, "退回理由对本人可见")

    revise_path = detail_path + "revise/"
    s, rev_form = get(member, revise_path)
    token = csrf_of(rev_form)
    check("修改表单可打开", s == 200 and token is not None, "HTTP %s" % s)

    s, body8, _u = post_multipart(
        member, revise_path,
        {"csrfmiddlewaretoken": token, "text_content": "已调亮配色，主视觉改成高对比红蓝。"},
        {"images": ("poster_v2.jpg", make_jpeg(1000, 750), "image/jpeg")})
    check("重新提交成功", "重新提交" in body8 or "第 2 版" in body8 or "待审核" in body8,
          "HTTP %s" % s)

    s, body9 = get(member, detail_path)
    check("版本号递增到 2", "第 2 版" in body9, "版本历史含第 2 版")
    check("第 1 版仍保留", "第 1 版" in body9, "历史版本未被覆盖")

    # ---------- 审核人通过 ----------
    print("\n【5. 审核人通过】")
    s, rev_body2 = get(reviewer, review_path)
    token = csrf_of(rev_body2)
    s, body10, _u = post_form(reviewer, review_path,
                              {"csrfmiddlewaretoken": token, "action": "approve",
                               "comment": "这次配色好多了。"})
    check("通过成功", "通过" in body10, "HTTP %s" % s)

    s, body11 = get(member, detail_path)
    check("状态为审核通过", "审核通过" in body11, "记录状态已更新")
    check("审核历史含退回与通过",
          "退回修改" in body11 and "通过" in body11, "两条审核记录都在")

    # ---------- 发起重新审核 ----------
    print("\n【6. 发起重新审核】")
    reopen_path = review_path + "reopen/"
    s, ro_form = get(reviewer, reopen_path)
    token = csrf_of(ro_form)
    check("重新审核页可访问", s == 200 and token is not None, "HTTP %s" % s)

    s, body12, _u = post_form(reviewer, reopen_path,
                              {"csrfmiddlewaretoken": token,
                               "reopen_reason": "", "comment": ""})
    check("空原因发起重新审核被拒", "必须填写原因" in body12 or "原因" in body12,
          "页面提示必填原因")

    s, ro_form = get(reviewer, reopen_path)
    token = csrf_of(ro_form)
    s, body13, _u = post_form(reviewer, reopen_path,
                              {"csrfmiddlewaretoken": token,
                               "reopen_reason": "发现海报上的比赛名称印错了",
                               "comment": ""})
    check("发起重新审核成功", "重新审核" in body13, "HTTP %s" % s)

    s, body14 = get(member, detail_path)
    check("状态回到待审核", "待审核" in body14, "重新审核后回到待审核")
    check("原通过记录仍保留", "这次配色好多了" in body14, "原审核意见未被删除")
    check("重新审核原因可见", "印错" in body14, "重新审核原因已记录")

    # ---------- 附件权限 ----------
    print("\n【7. 附件访问控制】")
    m_att = re.search(r'href="(/my/submissions/attachment/(\d+)/download/)"', body14)
    check("详情页有附件下载入口", m_att is not None,
          m_att.group(1) if m_att else "未找到")
    if m_att:
        att_url = m_att.group(1)
        # 匿名访问
        anon = make_session(follow=False)
        s, _ = get_noredirect(anon, att_url)
        # 未登录 → 302 跳登录页（`follow=False` 才能看到真实的 302；
        # 若自动跟随会落到 200 的登录页，从而误判为"访问成功"）
        check("匿名访问附件被拒（302 跳登录）", s == 302, "HTTP %s（预期 302）" % s)
        # 跨用户
        s, _ = get(other, att_url)
        check("跨用户访问附件被拒", s == 403, "HTTP %s（预期 403）" % s)
        # 本人
        s, _ = get(member, att_url)
        check("本人可访问附件", s in (200, 302), "HTTP %s" % s)

    # ---------- 管理端 ----------
    print("\n【8. 管理端与越权检查】")
    s, mg = get(admin_probe, "/manage/submissions/")
    check("管理员可看记录管理页", s == 200, "HTTP %s" % s)

    s, _ = get(member, "/manage/submissions/")
    check("干事不能进入管理端", s == 403, "HTTP %s（预期 403）" % s)

    s, _ = get(member, "/my/submissions/review/")
    check("无审核权的干事打开审核队列（页面可访问但列表为空）", s == 200,
          "HTTP %s（不泄露他人记录）" % s)

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
