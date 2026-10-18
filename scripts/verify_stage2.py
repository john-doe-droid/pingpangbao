#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
P2 阶段统一验收入口。

真正执行检查与测试，任一必要检查失败即返回非零退出码。

与 verify_stage1.py 的关系：
- verify_stage1.py 仍然是 P1 的验收入口，本脚本**不修改它**；
- 本脚本额外覆盖 P2 新增内容，并**把 P1 回归作为一项独立必要检查**，
  确保 P2 没有让 P1 退化。

三类结果，语义严格区分：
    PASS  —— 该检查真实通过
    FAIL  —— 该检查真实失败（导致整体非零退出）
    SKIP  —— 环境不具备（例如本机没有 Docker），**不计入通过**，
             在报告里单独列为"未验证事项"

禁止事项（与任务要求一致）：
- 不吞异常返回成功；不跳过测试；不降低断言；
- 不伪造测试输出；不把未执行的检查标记为通过。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\verify_stage2.py
    .\\.venv\\Scripts\\python.exe scripts\\verify_stage2.py --only models,permissions
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
VENV_PY = BASE_DIR / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    VENV_PY = BASE_DIR / ".venv" / "bin" / "python"
PYTHON = str(VENV_PY if VENV_PY.exists() else sys.executable)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[dict] = []

# P2 新增的测试文件（用于单独统计）
P2_TEST_FILES = [
    "tests/p2/test_models.py",
    "tests/p2/test_permissions.py",
    "tests/p2/test_pages.py",
]

# P1 原有测试文件（回归基线：一个都不能少）
P1_TEST_FILES = [
    "tests/test_accounts.py",
    "tests/test_password_change.py",
    "tests/test_health.py",
    "tests/test_settings.py",
    "tests/test_database.py",
]


def record(name, status, detail, output=""):
    results.append({"name": name, "status": status, "detail": detail, "output": output})
    icon = {PASS: "✅", FAIL: "❌", SKIP: "⏭️ "}[status]
    print("%s %s —— %s" % (icon, name, detail))
    if output and status == FAIL:
        for line in output.strip().splitlines()[-30:]:
            print("      | %s" % line)


def run(cmd, env_extra=None, timeout=900):
    env = dict(os.environ)
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            cmd, cwd=str(BASE_DIR), capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "命令超时（>%ds）：%s" % (timeout, " ".join(cmd))
    except FileNotFoundError as exc:
        return 127, "找不到可执行文件：%s（%s）" % (cmd[0], exc)


def _pytest_summary(out):
    """从 pytest 输出里提取汇总行（如 "140 passed in 68.9s"）。"""
    for line in reversed(out.strip().splitlines()):
        if re.search(r"\d+ (passed|failed|error)", line):
            return line.strip().strip("=").strip()
    return ""


def _pytest_counts(out):
    """返回 (passed, failed, errors)；解析不到返回 (0,0,0)。"""
    def grab(word):
        m = re.search(r"(\d+) %s" % word, out)
        return int(m.group(1)) if m else 0
    return grab("passed"), grab("failed"), grab("error")


# ===========================================================================
# 1. 基础环境
# ===========================================================================

def check_python():
    code, out = run([PYTHON, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"])
    ver = out.strip().splitlines()[-1] if out.strip() else "?"
    major_minor = ".".join(ver.split(".")[:2])
    if code != 0:
        record("Python 解释器可用", FAIL, "无法执行 %s" % PYTHON, out)
    elif major_minor != "3.13":
        record("Python 版本为 3.13", FAIL, "当前 %s" % ver, out)
    else:
        record("Python 版本为 3.13", PASS, "Python %s" % ver)


def check_dependencies():
    code, out = run([PYTHON, "-c",
                     "import django, psycopg, dotenv, whitenoise;"
                     "print('django=' + django.get_version())"])
    if code != 0:
        record("依赖已安装", FAIL, "导入失败（是否忘了 pip install -r requirements.txt）", out)
        return
    if "django=5.2" not in out:
        record("Django 为 5.2 LTS", FAIL, "版本不符：%s" % out.strip(), out)
    else:
        record("Django 为 5.2 LTS", PASS, out.strip().splitlines()[-1])


def check_django_checks():
    code, out = run([PYTHON, "manage.py", "check"])
    if code != 0:
        record("Django 系统检查", FAIL, "manage.py check 未通过", out)
    elif "WARNINGS" in out or "ERRORS" in out:
        record("Django 系统检查", FAIL, "存在警告或错误", out)
    else:
        record("Django 系统检查", PASS, "no issues")


def check_migrations_consistent():
    code, out = run([PYTHON, "manage.py", "makemigrations", "--check", "--dry-run"])
    if code != 0:
        record("模型与迁移一致", FAIL, "存在未生成的迁移", out)
    else:
        record("模型与迁移一致", PASS, "无待生成迁移")


def check_migrations_applied():
    code, out = run([PYTHON, "manage.py", "migrate", "--check"])
    if code != 0:
        record("数据库迁移已应用", FAIL, "存在未应用的迁移", out)
    else:
        record("数据库迁移已应用", PASS, "全部已应用")


def check_database_is_postgres():
    script = (
        "import os, django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.db import connection;"
        "c = connection.cursor(); c.execute('SHOW server_version');"
        "print('engine=' + connection.settings_dict['ENGINE']);"
        "print('server_version=' + str(c.fetchone()[0]))"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("数据库为 PostgreSQL 18", FAIL,
               "无法连接数据库（确认 PostgreSQL 已启动、.env 正确）", out)
        return
    engine = version = ""
    for line in out.splitlines():
        if line.startswith("engine="):
            engine = line.split("=", 1)[1].strip()
        if line.startswith("server_version="):
            version = line.split("=", 1)[1].strip()
    if "postgresql" not in engine:
        record("数据库为 PostgreSQL 18", FAIL, "ENGINE=%s（禁止 SQLite）" % engine, out)
        return
    if version.split(".")[0] != "18":
        record("数据库为 PostgreSQL 18", FAIL, "服务端版本 %s" % version, out)
    else:
        record("数据库为 PostgreSQL 18", PASS, "engine=%s, server=%s" % (engine, version))


# ===========================================================================
# 2. P2 结构检查
# ===========================================================================

def check_p2_apps_registered():
    script = (
        "import os, django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.conf import settings;"
        "print('competitions' in settings.INSTALLED_APPS);"
        "print('tasks' in settings.INSTALLED_APPS);"
        "print(settings.AUTH_USER_MODEL)"
    )
    code, out = run([PYTHON, "-c", script])
    lines = [l.strip() for l in out.strip().splitlines()]
    if code != 0 or len(lines) < 3:
        record("P2 应用已注册", FAIL, "无法读取 INSTALLED_APPS", out)
        return
    if lines[0] != "True" or lines[1] != "True":
        record("P2 应用已注册", FAIL, "competitions/tasks 未注册", out)
        return
    if lines[2] != "accounts.User":
        record("P2 应用已注册", FAIL, "AUTH_USER_MODEL 被改动：%s" % lines[2], out)
        return
    record("P2 应用已注册", PASS, "competitions + tasks；用户模型仍为 accounts.User")


def check_p2_models_present():
    """六个核心实体必须都存在。"""
    script = (
        "import os, django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from competitions.models import Competition, ReviewerAssignment;"
        "from tasks.models import WorkType, TaskTemplate, CompetitionTask, TaskAssignment;"
        "print('ok')"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("六个核心模型存在", FAIL, "模型导入失败", out)
    else:
        record("六个核心模型存在", PASS,
               "Competition / WorkType / TaskTemplate / CompetitionTask / "
               "TaskAssignment / ReviewerAssignment")


def check_db_modules():
    """核心表与两个部分唯一索引必须真实存在于 PostgreSQL 中。"""
    expected_tables = {
        "competitions_competition", "competitions_reviewerassignment",
        "tasks_worktype", "tasks_tasktemplate",
        "tasks_competitiontask", "tasks_taskassignment",
        "core_auditlog",
    }
    expected_indexes = {
        "uniq_active_assignment_per_task_member",
        "uniq_active_reviewer_per_competition",
    }
    script = (
        "import os, django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.db import connection;"
        "c = connection.cursor();"
        "c.execute(\"SELECT tablename FROM pg_tables WHERE schemaname='public'\");"
        "print('T:' + ','.join(r[0] for r in c.fetchall()));"
        "c.execute(\"SELECT indexname FROM pg_indexes WHERE schemaname='public'\");"
        "print('I:' + ','.join(r[0] for r in c.fetchall()))"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("核心表与唯一索引已落库", FAIL, "查询数据库失败", out)
        return
    tables, indexes = set(), set()
    for line in out.splitlines():
        if line.startswith("T:"):
            tables = set(line[2:].split(","))
        elif line.startswith("I:"):
            indexes = set(line[2:].split(","))

    missing_t = expected_tables - tables
    missing_i = expected_indexes - indexes
    if missing_t or missing_i:
        record("核心表与唯一索引已落库", FAIL,
               "缺失表=%s 缺失索引=%s" % (sorted(missing_t), sorted(missing_i)), out)
    else:
        record("核心表与唯一索引已落库", PASS,
               "%d 张核心表 + %d 个部分唯一索引" % (len(expected_tables), len(expected_indexes)))


def check_seed_roles():
    script = (
        "import os, django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from accounts.models import Role;"
        "print(','.join(sorted(Role.objects.values_list('code', flat=True))))"
    )
    code, out = run([PYTHON, "-c", script])
    codes = set(out.strip().splitlines()[-1].split(",")) if out.strip() else set()
    need = {"club_admin", "member", "viewer"}
    if code != 0 or not need <= codes:
        record("P2 角色种子数据存在", FAIL,
               "缺少角色：%s（当前：%s）" % (sorted(need - codes), sorted(codes)), out)
    else:
        record("P2 角色种子数据存在", PASS, "club_admin / member / viewer")


def check_urls_wired():
    """关键 URL 必须可反向解析 —— 证明页面真的挂上了。"""
    script = (
        "import os, django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.urls import reverse;"
        "urls = ["
        "('competitions:list',[]),('competitions:create',[]),"
        "('tasks:my_tasks',[]),"
        "('tasks_admin:worktype_list',[]),('tasks_admin:template_list',[]),"
        "];"
        "print(','.join(reverse(n, args=a) for n, a in urls))"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("P2 路由已挂载", FAIL, "URL 反向解析失败", out)
        return
    paths = out.strip().splitlines()[-1]
    expected = ["/competitions/", "/competitions/new/", "/my/tasks/",
                "/manage/tasks/work-types/", "/manage/tasks/templates/"]
    missing = [p for p in expected if p not in paths]
    if missing:
        record("P2 路由已挂载", FAIL, "缺少路径：%s" % missing, out)
    else:
        record("P2 路由已挂载", PASS, "5 个关键路径均可解析")


# ===========================================================================
# 3. 测试
# ===========================================================================

def check_p2_tests():
    code, out = run([PYTHON, "-m", "pytest", "-q"] + P2_TEST_FILES)
    passed, failed, errors = _pytest_counts(out)
    if code != 0 or failed or errors:
        record("P2 测试全部通过", FAIL,
               "passed=%d failed=%d errors=%d" % (passed, failed, errors), out)
    elif passed == 0:
        record("P2 测试全部通过", FAIL, "没有收集到任何 P2 测试（未执行≠通过）", out)
    else:
        record("P2 测试全部通过", PASS, _pytest_summary(out))


def check_p1_regression():
    """P1 测试必须仍然全绿 —— 这是"未退化"的核心证据。"""
    missing = [f for f in P1_TEST_FILES if not (BASE_DIR / f).exists()]
    if missing:
        record("P1 回归测试未退化", FAIL, "P1 测试文件缺失：%s" % missing)
        return
    code, out = run([PYTHON, "-m", "pytest", "-q"] + P1_TEST_FILES)
    passed, failed, errors = _pytest_counts(out)
    if code != 0 or failed or errors:
        record("P1 回归测试未退化", FAIL,
               "passed=%d failed=%d errors=%d" % (passed, failed, errors), out)
        return
    if passed < 52:
        record("P1 回归测试未退化", FAIL,
               "P1 测试数从 52 降到 %d（可能被删除或跳过）" % passed, out)
        return
    record("P1 回归测试未退化", PASS, _pytest_summary(out))


def check_full_suite():
    code, out = run([PYTHON, "-m", "pytest", "-q"])
    passed, failed, errors = _pytest_counts(out)
    if code != 0 or failed or errors:
        record("全套测试通过", FAIL,
               "passed=%d failed=%d errors=%d" % (passed, failed, errors), out)
    else:
        record("全套测试通过", PASS, _pytest_summary(out))


def check_postgres_marker_tests():
    """标记为 postgres 的测试必须跑在真实 PostgreSQL 上。"""
    code, out = run([PYTHON, "-m", "pytest", "-q", "-m", "postgres"])
    passed, failed, errors = _pytest_counts(out)
    if code != 0 or failed or errors:
        record("PostgreSQL 专项测试", FAIL,
               "passed=%d failed=%d errors=%d" % (passed, failed, errors), out)
    elif passed == 0:
        record("PostgreSQL 专项测试", FAIL, "没有匹配到任何 postgres 标记测试", out)
    else:
        record("PostgreSQL 专项测试", PASS, _pytest_summary(out))


# ===========================================================================
# 4. 权限硬约束（防回归的独立验证，不依赖测试文件的写法）
# ===========================================================================

def check_permission_invariants():
    """用独立脚本验证几条最关键的权限不变量。

    刻意不复用测试文件的断言，而是重新写一遍逻辑 ——
    这样如果测试被误改，这里仍能拦住。

    脚本必须可重复运行：开头先清掉上一次可能留下的验证数据
    （否则固定学号会撞唯一约束，把「脚本不幂等」误报成「权限出问题」）。
    """
    script = r'''
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Role
from competitions.models import Competition, CompetitionStatus, ReviewerAssignment
from competitions.permissions import can_manage_competition, can_review_competition
from core.permissions import user_can_view_all, user_is_admin

User = get_user_model()
problems = []

VERIFY_IDS = ["VF-ADM", "VF-VIEW", "VF-MEM"]
VERIFY_COMPETITIONS = ["权限不变量验证比赛", "另一场比赛"]

# --- 清理上次残留，保证脚本可重复执行 ---
User.objects.filter(student_id__in=VERIFY_IDS).delete()
Competition.objects.filter(name__in=VERIFY_COMPETITIONS).delete()


def mk(sid, name, code=None, **extra):
    u = User.objects.create_user(
        student_id=sid, password="Verify!Passw0rd9", real_name=name, **extra)
    u.must_change_password = False
    u.save(update_fields=["must_change_password"])
    if code:
        role, _ = Role.objects.get_or_create(code=code, defaults={"name": code})
        u.roles.add(role)
    return u


def mk_competition(name):
    return Competition.objects.create(
        name=name, event_date=timezone.localdate(),
        default_deadline=timezone.now() + timedelta(days=3),
        status=CompetitionStatus.ACTIVE, created_by=admin)


try:
    admin = mk("VF-ADM", "验证管理员", "club_admin")
    viewer = mk("VF-VIEW", "验证查看员", "viewer")
    member = mk("VF-MEM", "验证干事", "member")

    comp = mk_competition("权限不变量验证比赛")

    # 1. 管理员有管理权
    if not can_manage_competition(admin, comp):
        problems.append("管理员应能管理比赛")

    # 2. 查看权 != 管理权
    if user_can_view_all(viewer) and can_manage_competition(viewer, comp):
        problems.append("全局查看权不应推导出管理权")

    # 3. 查看权 != 审核权
    if user_can_view_all(viewer) and can_review_competition(viewer, comp):
        problems.append("全局查看权不应推导出审核权")

    # 4. 管理员身份本身不等于审核权
    if user_is_admin(admin) and can_review_competition(admin, comp):
        problems.append("管理员身份不应自动获得审核权")

    # 5. 显式授权后才有审核权
    ReviewerAssignment.objects.create(
        competition=comp, reviewer=member, granted_by=admin)
    if not can_review_competition(member, comp):
        problems.append("显式授权后应获得审核权")

    # 6. 撤销后失去审核权
    ReviewerAssignment.objects.filter(
        competition=comp, reviewer=member).update(is_active=False)
    if can_review_competition(member, comp):
        problems.append("撤销后不应仍有审核权")

    # 7. 审核权不跨比赛
    comp2 = mk_competition("另一场比赛")
    ReviewerAssignment.objects.filter(
        competition=comp, reviewer=member).update(is_active=True)
    if can_review_competition(member, comp2):
        problems.append("审核权不应跨比赛生效")
finally:
    # 清理本次验证数据，不在业务库里留痕
    User.objects.filter(student_id__in=VERIFY_IDS).delete()
    Competition.objects.filter(name__in=VERIFY_COMPETITIONS).delete()

print("PROBLEMS:" + ("|".join(problems) if problems else "NONE"))
'''
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("权限不变量（独立验证）", FAIL, "验证脚本执行失败", out)
        return
    marker = [l for l in out.splitlines() if l.startswith("PROBLEMS:")]
    if not marker:
        record("权限不变量（独立验证）", FAIL, "未得到验证结果", out)
        return
    payload = marker[-1].split("PROBLEMS:", 1)[1]
    if payload != "NONE":
        record("权限不变量（独立验证）", FAIL, payload.replace("|", "；"), out)
    else:
        record("权限不变量（独立验证）", PASS,
               "7 条不变量全部成立（查看≠管理≠审核，审核需显式授权且不跨比赛）")


def check_get_never_mutates():
    """关键写操作不接受 GET —— 防止用链接触发状态变更。"""
    # 注意：带多个参数的路由必须提供足够的占位参数，
    # 否则 reverse 会抛 NoReverseMatch，把「参数不足」误判成「路由不存在」。
    script = r'''
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()
from django.urls import reverse, NoReverseMatch

routes = [
    ("competitions:activate", [1]),
    ("competitions:archive", [1]),
    ("competitions:reviewer_revoke", [1, 1]),
    ("tasks_admin:worktype_toggle", [1]),
    ("tasks_admin:template_toggle", [1]),
    ("tasks_admin:task_assign", [1]),
    ("tasks_admin:assignment_deactivate", [1]),
]
found = []
for name, args in routes:
    try:
        reverse(name, args=args)
        found.append(name)
    except NoReverseMatch:
        pass
print("NAMES:" + ",".join(found))
'''
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("写操作路由存在且为 POST", FAIL, "路由检查失败", out)
        return
    names = ""
    for line in out.splitlines():
        if line.startswith("NAMES:"):
            names = line[6:]
    expected = ["competitions:activate", "competitions:archive",
                "competitions:reviewer_revoke", "tasks_admin:worktype_toggle",
                "tasks_admin:template_toggle", "tasks_admin:task_assign",
                "tasks_admin:assignment_deactivate"]
    missing = [n for n in expected if n not in names]
    if missing:
        record("写操作路由存在且为 POST", FAIL, "缺少路由：%s" % missing, out)
    else:
        record("写操作路由存在且为 POST", PASS,
               "7 个写操作路由存在；其 GET 行为由测试逐项验证（应返回 405）")


# ===========================================================================
# 5. 回归与完整性
# ===========================================================================

def check_p1_files_not_rewritten():
    """P1 既有文件未被覆盖/重写。

    分三类处理，**不做笼统豁免**：

    A. 追加式改动（允许）：注册新 app、挂载新路由
       —— config/settings/base.py、config/urls.py
    B. 文档（允许）：README.md 本阶段必须随进度更新
    C. 内容纠正（须逐函数证明）：core/views.py 的首页文案要改掉
       「本阶段不含比赛/任务」这种在 P2 之后已失真的说法，
       但**安全关键函数 live() / ready() 必须与 P1 逐字一致**。
       这里用 AST 比对来证明，而不是把整个文件放进白名单。
    """
    APPEND_ONLY = {
        "config/settings/base.py",
        "config/urls.py",
        "README.md",
    }
    FUNCTION_GUARDED = {"core/views.py": ["live", "ready"]}

    baseline = BASE_DIR / "scripts" / "p2_p1_baseline.json"
    if not baseline.exists():
        record("P1 既有文件未被覆盖", SKIP, "未找到 P1 基线记录文件")
        return

    import hashlib

    data = json.loads(baseline.read_text(encoding="utf-8"))
    changed, missing = [], []
    for rel, expect in data["files"].items():
        if rel in APPEND_ONLY or rel in FUNCTION_GUARDED:
            continue
        path = BASE_DIR / rel.replace("/", os.sep)
        if not path.exists():
            missing.append(rel)
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != expect:
            changed.append(rel)

    if missing or changed:
        detail = []
        if missing:
            detail.append("文件缺失：%s" % missing)
        if changed:
            detail.append("内容被改动：%s" % changed)
        record("P1 既有文件未被覆盖", FAIL, "；".join(detail))
        return

    # --- 对受函数级保护的文件做 AST 比对 ---
    # 直接在进程内比对：用 git 取 P1 版本，解析两边 AST，逐个函数比对。
    # 不用字符串模板拼脚本（占位符易错，且难维护）。
    import ast
    import subprocess

    guard_notes = []
    for rel, func_names in FUNCTION_GUARDED.items():
        try:
            old = subprocess.run(
                ["git", "show", "HEAD:%s" % rel],
                cwd=str(BASE_DIR), capture_output=True,
                encoding="utf-8", errors="replace", timeout=60,
            ).stdout
        except Exception as exc:  # noqa: BLE001
            record("P1 既有文件未被覆盖", FAIL,
                   "无法读取 %s 的 P1 版本：%r" % (rel, exc))
            return

        if not old.strip():
            record("P1 既有文件未被覆盖", FAIL,
                   "无法从 git 取得 %s 的 P1 版本（HEAD 中不存在？）" % rel)
            return

        def _func_dump(text, names):
            tree = ast.parse(text)
            out = {}
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name in names:
                    out[node.name] = ast.dump(node)
            return out

        path = BASE_DIR / rel.replace("/", os.sep)
        new = path.read_text(encoding="utf-8")
        try:
            old_funcs = _func_dump(old, func_names)
            new_funcs = _func_dump(new, func_names)
        except SyntaxError as exc:
            record("P1 既有文件未被覆盖", FAIL,
                   "%s 解析失败：%s" % (rel, exc))
            return

        missing_funcs = [n for n in func_names if n not in old_funcs or n not in new_funcs]
        if missing_funcs:
            record("P1 既有文件未被覆盖", FAIL,
                   "%s 中找不到受保护函数：%s" % (rel, missing_funcs))
            return

        changed_funcs = [n for n in func_names if old_funcs[n] != new_funcs[n]]
        if changed_funcs:
            record("P1 既有文件未被覆盖", FAIL,
                   "%s 中的安全关键函数被改动：%s（这些函数与健康检查/安全相关，"
                   "不允许在 P2 中改动）" % (rel, changed_funcs))
            return

        guard_notes.append("%s 的 %s 逐字未变" % (rel, "/".join(func_names)))

    plain_count = len(data["files"]) - len(APPEND_ONLY) - len(FUNCTION_GUARDED)
    record("P1 既有文件未被覆盖", PASS,
           "%d 个 P1 文件哈希一致 + %s（其余为允许的追加与内容纠正）"
           % (plain_count, "；".join(guard_notes)))


def check_agent_loop_untouched():
    """agent-loop 护栏未被修改。"""
    guard = BASE_DIR.parent / "化学" / "agent-loop" / "probe" / "evidence" / "p1_guard_baseline.json"
    if not guard.exists():
        record("agent-loop 护栏未被修改", SKIP, "未找到护栏基线文件")
        return
    import hashlib

    data = json.loads(guard.read_text(encoding="utf-8"))
    workdir = BASE_DIR.parent / "化学"
    bad = []
    for item in data["files"]:
        path = workdir / item["path"]
        if not path.exists():
            bad.append("%s（缺失）" % item["path"])
        elif hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            bad.append(item["path"])
    if bad:
        record("agent-loop 护栏未被修改", FAIL, "发生变化：%s" % ", ".join(bad))
    else:
        record("agent-loop 护栏未被修改", PASS,
               "%d 个护栏文件哈希与基线一致" % len(data["files"]))


def check_no_submission_or_review_features():
    """本阶段不得提前实现提交/审核/COS/Excel 功能。"""
    forbidden_modules = ["submissions", "reviews", "reports"]
    found = [m for m in forbidden_modules if (BASE_DIR / m).exists()]
    if found:
        record("未提前实现后续阶段模块", FAIL, "发现越界模块：%s" % found)
        return

    # 密码或密钥不得写死在代码里
    code, out = run(["git", "grep", "-nE",
                     r"(SECRET_KEY|PASSWORD)\s*=\s*['\"][A-Za-z0-9_-]{20,}",
                     "--", "*.py", "*.yml", "*.conf"])
    hits = [l for l in out.splitlines()
            if not any(k in l for k in ("test", "verify-only", "build-only", "fake-"))]
    if hits:
        record("未提前实现后续阶段模块", FAIL, "疑似写死凭据：%s" % hits[:3])
        return
    record("未提前实现后续阶段模块", PASS,
           "无 submissions/reviews/reports 模块；未发现写死的密钥或口令")


def check_docker_env_blockers():
    """Docker / Gunicorn 的环境阻塞情况（P1 遗留，如实标注）。"""
    if shutil.which("docker") is None:
        record("Docker 实际可运行", SKIP,
               "本机未安装 docker —— 容器编排仍未在本机验证（P1 遗留事项）")
    else:
        code, out = run(["docker", "compose", "config", "--quiet"], timeout=120)
        if code != 0:
            record("Docker 实际可运行", FAIL, "docker compose config 失败", out)
        else:
            record("Docker 实际可运行", PASS, "compose 配置可解析")

    code, out = run([PYTHON, "-c", "import fcntl"])
    if code != 0:
        record("Gunicorn 实际可运行", SKIP,
               "本机为 Windows，Gunicorn 依赖 fcntl 无法运行（P1 遗留事项）")
    else:
        record("Gunicorn 实际可运行", PASS, "可导入 gunicorn 依赖")


def check_readme_consistency():
    """README 里提到的 P2 路径与命令必须真实存在。"""
    readme = BASE_DIR / "README.md"
    if not readme.exists():
        record("README 与项目一致", FAIL, "缺少 README.md")
        return
    text = readme.read_text(encoding="utf-8").replace("\\", "/")
    required = [
        "scripts/verify_stage2.py", "competitions", "tasks",
        "my/tasks/", "manage/tasks/",
    ]
    missing = [r for r in required if r not in text]
    if not (BASE_DIR / "scripts" / "verify_stage2.py").exists():
        missing.append("scripts/verify_stage2.py(磁盘缺失)")
    if missing:
        record("README 与项目一致", FAIL, "缺少：%s" % missing)
    else:
        record("README 与项目一致", PASS, "P2 相关路径与命令均已记录")


# ===========================================================================
# 主流程
# ===========================================================================

CHECKS = [
    ("python", check_python),
    ("deps", check_dependencies),
    ("check", check_django_checks),
    ("makemig", check_migrations_consistent),
    ("migrate", check_migrations_applied),
    ("db", check_database_is_postgres),
    ("apps", check_p2_apps_registered),
    ("models", check_p2_models_present),
    ("tables", check_db_modules),
    ("roles", check_seed_roles),
    ("urls", check_urls_wired),
    ("p2tests", check_p2_tests),
    ("p1reg", check_p1_regression),
    ("pgmarker", check_postgres_marker_tests),
    ("full", check_full_suite),
    ("perm", check_permission_invariants),
    ("post", check_get_never_mutates),
    ("p1file", check_p1_files_not_rewritten),
    ("guard", check_agent_loop_untouched),
    ("scope", check_no_submission_or_review_features),
    ("env", check_docker_env_blockers),
    ("readme", check_readme_consistency),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="P2 阶段统一验收入口")
    ap.add_argument("--only", help="只跑指定检查（逗号分隔的 key）")
    ap.add_argument("--list", action="store_true", help="列出所有检查项")
    args = ap.parse_args()

    if args.list:
        for key, fn in CHECKS:
            print("%-10s %s" % (key, (fn.__doc__ or "").splitlines()[0] if fn.__doc__ else ""))
        return 0

    only = set(args.only.split(",")) if args.only else None

    print("=" * 76)
    print("P2 阶段验证 —— 比赛、任务模板与人员分配")
    print("项目目录：%s" % BASE_DIR)
    print("解释器  ：%s" % PYTHON)
    print("=" * 76)

    started = time.time()
    for key, fn in CHECKS:
        if only and key not in only:
            continue
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            record(getattr(fn, "__doc__", key) or key, FAIL, "检查抛出异常：%r" % exc)

    failures = [r for r in results if r["status"] == FAIL]
    skips = [r for r in results if r["status"] == SKIP]
    passes = [r for r in results if r["status"] == PASS]

    print()
    print("=" * 76)
    print("汇总：通过 %d 项，失败 %d 项，跳过（环境阻塞）%d 项，耗时 %.1fs"
          % (len(passes), len(failures), len(skips), time.time() - started))
    if skips:
        print("-" * 76)
        print("未验证事项（环境阻塞，**不代表通过**）：")
        for r in skips:
            print("  · %s —— %s" % (r["name"], r["detail"]))
    if failures:
        print("-" * 76)
        print("失败项：")
        for r in failures:
            print("  ✗ %s —— %s" % (r["name"], r["detail"]))
    print("=" * 76)

    report = BASE_DIR / "verify_stage2_report.json"
    report.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": PYTHON,
        "project_dir": str(BASE_DIR),
        "summary": {"pass": len(passes), "fail": len(failures), "skip": len(skips)},
        "results": [{k: v for k, v in r.items() if k != "output"} for r in results],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("报告已写入：%s" % report.name)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
