#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地预览服务（仅用于视觉验证，非生产部署方式）。

用标准库 wsgiref 托管 config.wsgi:application，便于在本机用浏览器
检查桌面/移动端布局。**这不是生产运行方式**（生产用 Gunicorn + Nginx）。

用法：
    python scripts/preview_server.py [端口]
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8030
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

    import django

    django.setup()

    from wsgiref.simple_server import make_server

    from config.wsgi import application

    print("预览服务启动于 http://127.0.0.1:%d" % port)
    print("（仅用于本地视觉检查，不是生产运行方式）")
    make_server("127.0.0.1", port, application).serve_forever()
