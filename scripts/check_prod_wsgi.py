#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生产入口冒烟检查。

作用：验证 `config.wsgi:application`（也就是 Dockerfile 里 Gunicorn 调用的那个
入口）在**生产配置**下能正常服务首页、健康检查与静态资源。

为什么不用 Gunicorn 直接跑：
    Gunicorn 依赖 Unix 专有的 `fcntl` 模块，**无法在 Windows 上运行**。
    本机没有 Docker 也没有 Linux 环境，因此这里改用标准库 wsgiref
    直接以 WSGI 协议调用同一个 application 对象 —— 被验证的入口代码是同一个，
    只是省掉了 Gunicorn 的进程管理外壳（那部分只能在 Linux 上验证）。

    **这不等于"Gunicorn 已在本机验证通过"**，报告里会如实标注。

用法（需要环境变量提供生产配置所需的连接参数）：
    python scripts/check_prod_wsgi.py
退出码：0 全部通过；1 有失败。
"""
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def call(application, path, host="127.0.0.1"):
    """按 WSGI 协议调用应用。"""
    environ = {
        "REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": "",
        "SERVER_NAME": host, "SERVER_PORT": "80", "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": host, "wsgi.version": (1, 0), "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(b""), "wsgi.errors": io.StringIO(),
        "wsgi.multithread": False, "wsgi.multiprocess": False, "wsgi.run_once": False,
        "REMOTE_ADDR": "127.0.0.1",
    }
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(application(environ, start_response))
    return captured.get("status", "500 ?"), captured.get("headers", {}), body


def main() -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

    import django

    django.setup()

    from django.conf import settings

    application = __import__("config.wsgi", fromlist=["application"]).application

    failures = []

    # 生产配置必须是"安全形态"：DEBUG 关、非 SQLite
    if settings.DEBUG is not False:
        failures.append("生产配置下 DEBUG 不是 False")
    if "postgresql" not in settings.DATABASES["default"]["ENGINE"]:
        failures.append("生产配置未使用 PostgreSQL")

    print("生产 WSGI 入口：config.wsgi:application")
    print("DEBUG=%s  ENGINE=%s" % (
        settings.DEBUG,
        settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1],
    ))
    print()

    # --- 关键页面 ---
    expected_status = {
        "/health/live": 200,
        "/health/ready": 200,
        "/": 302,
        "/accounts/login/": 200,
    }
    for path, expected in expected_status.items():
        status, _hd, body = call(application, path)
        code = int(status.split()[0])
        print("  %-24s %-10s %6dB" % (path, status, len(body)))
        if code != expected:
            failures.append("%s 返回 %s（预期 %s）" % (path, status, expected))

    # --- 静态资源（生产下应为 manifest 哈希路径）---
    _st, _hd, body = call(application, "/accounts/login/")
    assets = re.findall(r'/static/[^"\']+', body.decode("utf-8", "replace"))
    print("\n  首页引用静态资源 %d 个：" % len(assets))
    if not assets:
        failures.append("首页没有引用任何静态资源（模板可能未正确渲染）")
    hashed = 0
    for url in assets:
        status, _hd, data = call(application, url)
        code = int(status.split()[0])
        print("    %-58s %s" % (url, status))
        if code != 200:
            failures.append("静态资源 %s 返回 %s" % (url, status))
        # manifest 存储会产出形如 site.ef6d98af208a.css 的哈希名
        if re.search(r"\.[0-9a-f]{12}\.(css|js)$", url):
            hashed += 1
    if assets and hashed == 0:
        failures.append(
            "静态资源未使用哈希名 —— manifest 可能未生成（collectstatic 未用 build 配置？）"
        )

    # --- 安全响应头 ---
    _st, headers, _b = call(application, "/accounts/login/")
    print("\n  安全响应头：")
    for header in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
        value = headers.get(header)
        print("    %-26s %s" % (header, value or "(缺失)"))
        if not value:
            failures.append("缺少安全响应头 %s" % header)

    print()
    if failures:
        print("生产入口检查失败：")
        for item in failures:
            print("  ✗ %s" % item)
        return 1

    print("生产入口检查通过（注意：Gunicorn 进程本身未在 Windows 上验证）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
