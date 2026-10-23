"""模板语法批量检查：所有模板都能被 Django 加载器解析。

用途：改版会大量改模板，一个漏掉的 {% endif %} 在生产上就是 500。
这个脚本把「模板能不能编译」变成一条可重复执行的检查。

用法：python scripts/check_templates.py
"""
import glob
import re
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")


def to_template_name(path: str) -> str:
    """把磁盘路径转成 Django 模板名（templates/ 之后的部分）。"""
    parts = path.replace("\\", "/").split("/")
    if "templates" in parts:
        return "/".join(parts[parts.index("templates") + 1:])
    return path


def main() -> int:
    import django

    django.setup()
    from django.template.loader import get_template

    files = sorted(
        glob.glob(str(BASE_DIR / "templates" / "*.html"))
        + glob.glob(str(BASE_DIR / "*" / "templates" / "**" / "*.html"), recursive=True)
    )

    bad = []
    lint = []
    for f in files:
        rel = to_template_name(f)
        src = Path(f).read_text(encoding="utf-8")

        # 多行 {# #} 是**静默**故障源：Django 的 {# #} 只支持单行，
        # 多写一行就会把后续内容当正文输出，破坏 HTML 结构且不报错。
        # 本项目已因此踩坑两次，所以在这里做成硬性检查。
        for m in re.finditer(r"\{#(?:(?!#\}).)*?\n(?:(?!#\}).)*?#\}", src, re.S):
            line = src[: m.start()].count("\n") + 1
            lint.append((rel, line, m.group(0)[:60].replace("\n", " / ")))

        try:
            get_template(rel)
        except Exception as exc:  # noqa: BLE001
            bad.append((rel, type(exc).__name__, str(exc)[:160]))

    for rel, line, snippet in lint:
        print("❌ %s:%d 多行 {# #} 注释 —— 请改用 comment 块" % (rel, line))
        print("     %s" % snippet)
    for rel, kind, msg in bad:
        print("❌ %-52s %s: %s" % (rel, kind, msg))

    print()
    print("检查 %d 个模板：语法错误 %d 个，多行注释 %d 处"
          % (len(files), len(bad), len(lint)))
    return 1 if (bad or lint) else 0


if __name__ == "__main__":
    sys.exit(main())
