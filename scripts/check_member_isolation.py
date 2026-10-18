# -*- coding: utf-8 -*-
"""干事端隔离的 HTTP 级实测（真实登录会话 + 真实请求）。

目的：验证 P2 最关键的一条要求 —— 干事只能看到分配给自己的任务，
且**直接改 URL 也拿不到别人的数据**。

这里刻意用真实的登录表单（带 CSRF）而不是 Django 测试客户端，
证明的是"真正跑起来的服务"上的行为，而不只是测试环境里的行为。
"""
import http.cookiejar
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8040"
PASSWORD = "DemoPassw0rd!2026"

results = []


def make_session():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(opener, path):
    """返回 (status, body)。4xx/5xx 也返回 body 而不抛异常。"""
    try:
        resp = opener.open(BASE + path, timeout=15)
        return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def login(opener, student_id):
    """走真实登录表单：先取 CSRF token，再 POST。"""
    _status, body = get(opener, "/accounts/login/")
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body)
    if not m:
        return False, "登录页取不到 CSRF token"
    token = m.group(1)

    data = urllib.parse.urlencode({
        "csrfmiddlewaretoken": token,
        "username": student_id,
        "password": PASSWORD,
    }).encode("utf-8")

    req = urllib.request.Request(BASE + "/accounts/login/", data=data)
    req.add_header("Referer", BASE + "/accounts/login/")
    try:
        resp = opener.open(req, timeout=15)
        return resp.status == 200, "登录后跳转到 %s" % resp.url
    except urllib.error.HTTPError as exc:
        return False, "HTTP %s" % exc.code


def check(name, ok, detail):
    results.append((name, bool(ok), detail))
    print("  %s %s —— %s" % ("✅" if ok else "❌", name, detail))


def main():
    print("=" * 74)
    print("干事端隔离实测（真实 HTTP 会话）  目标：%s" % BASE)
    print("=" * 74)

    # ---------- 干事甲：被分配任务 1 与任务 2 ----------
    print("\n【演示干事甲 D-MEMBER1（被分配任务 1、任务 2）】")
    s1 = make_session()
    ok, detail = login(s1, "D-MEMBER1")
    check("干事甲登录", ok, detail)

    status, body = get(s1, "/my/tasks/")
    check("我的任务页可访问", status == 200, "HTTP %s" % status)
    check("能看到自己的任务 1", "制作赛前海报" in body, "任务 1 出现在列表中")
    check("能看到自己的任务 2", "撰写赛后推文" in body, "任务 2 出现在列表中")

    status, _ = get(s1, "/my/tasks/mine/1/")
    check("能打开自己的任务 1 详情", status == 200, "HTTP %s" % status)

    # 干事甲不得进入管理端
    for path, label in [
        ("/manage/tasks/templates/", "任务模板管理页"),
        ("/manage/tasks/work-types/", "工作类型管理页"),
        ("/competitions/new/", "新建比赛页"),
        ("/manage/tasks/tasks/1/", "任务分配管理页"),
        ("/competitions/5/reviewers/", "审核人授权页"),
    ]:
        status, _ = get(s1, path)
        check("干事甲不能进入%s" % label, status == 403, "HTTP %s" % status)

    # ---------- 干事乙：只被分配任务 1 ----------
    print("\n【演示干事乙 D-MEMBER2（只被分配任务 1）】")
    s2 = make_session()
    ok, detail = login(s2, "D-MEMBER2")
    check("干事乙登录", ok, detail)

    status, body = get(s2, "/my/tasks/")
    check("我的任务页可访问", status == 200, "HTTP %s" % status)
    check("能看到自己的任务 1", "制作赛前海报" in body, "任务 1 在列表中")
    check("看不到别人（干事甲）的任务 2", "撰写赛后推文" not in body,
          "任务 2 未出现在干事乙的列表里")

    # 关键：直接改 URL 去拿别人任务
    status, _ = get(s2, "/my/tasks/mine/2/")
    check("改 URL 偷看他人任务 2 被拒", status == 403, "HTTP %s（预期 403）" % status)

    status, _ = get(s2, "/my/tasks/mine/1/")
    check("打开自己的任务 1 正常", status == 200, "HTTP %s" % status)

    # ---------- 查看员：有查看权但无管理权、无审核权 ----------
    print("\n【演示查看员 D-VIEWER（全局查看）】")
    s3 = make_session()
    ok, detail = login(s3, "D-VIEWER")
    check("查看员登录", ok, detail)

    status, _ = get(s3, "/competitions/")
    check("查看员能看比赛列表", status == 200, "HTTP %s" % status)

    for path, label in [
        ("/competitions/new/", "新建比赛页"),
        ("/manage/tasks/templates/", "任务模板管理页"),
        ("/competitions/5/reviewers/", "审核人授权页"),
    ]:
        status, _ = get(s3, path)
        check("查看员不能进入%s" % label, status == 403, "HTTP %s" % status)

    # ---------- 未登录 ----------
    print("\n【未登录】")
    anon = make_session()
    for path, label in [
        ("/my/tasks/", "我的任务"),
        ("/competitions/", "比赛管理"),
        ("/manage/tasks/templates/", "任务模板"),
    ]:
        status, body = get(anon, path)
        # 未登录应被重定向到登录页（urllib 会自动跟随，最终落到登录页）
        redirected = "/accounts/login/" in body or "请输入学号" in body
        check("未登录访问%s会跳登录" % label,
              status == 200 and redirected, "HTTP %s，落到登录页=%s" % (status, redirected))

    # ---------- 汇总 ----------
    fails = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 74)
    print("合计 %d 项，通过 %d 项，失败 %d 项"
          % (len(results), len(results) - len(fails), len(fails)))
    if fails:
        print("失败项：")
        for n in fails:
            print("  ✗ %s" % n)
    print("=" * 74)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
