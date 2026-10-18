#!/usr/bin/env python
"""Django 命令行入口。"""
import os
import sys


def main():
    # 默认使用开发配置；生产环境必须显式设置 DJANGO_SETTINGS_MODULE=config.settings.prod
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "无法导入 Django。请确认已激活虚拟环境并安装依赖：\n"
            "    .\\.venv\\Scripts\\activate\n"
            "    pip install -r requirements.txt"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
