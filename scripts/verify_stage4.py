#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P4 阶段统一验收入口。

真正执行检查与测试，任一必要检查失败即返回非零退出码。

三类结果语义（与 P1/P2/P3 的入口一致）：
    PASS  —— 真实通过
    FAIL  —— 真实失败（导致整体非零退出）
    SKIP  —— 环境不具备，**不计入通过**，单独列为"未验证事项"

禁止：吞异常返回成功、跳过测试、降低断言、伪造输出、把未执行标为通过。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\verify_stage4.py
    .\\.venv\\Scripts\\python.exe scripts\\verify_stage4.py --only p4tests,counting
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

P4_TEST_DIR = "tests/p4"
P1_TEST_FILES = [
    "tests/test_accounts.py", "tests/test_password_change.py",
    "tests/test_health.py", "tests/test_settings.py", "tests/test_database.py",
]
P2_TEST_FILES = [
    "tests/p2/test_models.py", "tests/p2/test_permissions.py", "tests/p2/test_pages.py",
]
P3_TEST_FILES = [
    "tests/p3/test_imaging.py", "tests/p3/test_quota_and_upload.py",
    "tests/p3/test_submission_review.py", "tests/p3/test_file_video_and_cleanup.py",
]


def record(name, status, detail, output=""):
    results.append({"name": name, "status": status, "detail": detail, "output": output})
    icon = {PASS: "✅", FAIL: "❌", SKIP: "⏭️ "}[status]
    print("%s %s —— %s" % (icon, name, detail))
    if output and status == FAIL:
        for line in output.strip().splitlines()[-30:]:
            print("      | %s" % line)


def run(cmd, env_extra=None, timeout=1800):
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


def _counts(out):
    def grab(word):
        m = re.search(r"(\d+) %s" % word, out)
        return int(m.group(1)) if m else 0
    return grab("passed"), grab("failed"), grab("error"), grab("skipped")


def _summary(out):
    for line in reversed(out.strip().splitlines()):
        if re.search(r"\d+ (passed|failed|error)", line):
            return line.strip().strip("=").strip()
    return ""


# ===========================================================================
# 环境与依赖
# ===========================================================================

def check_python():
    """Python 版本。"""
    code, out = run([PYTHON, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"])
    ver = out.strip().splitlines()[-1] if out.strip() else "?"
    if code != 0:
        record("Python 解释器可用", FAIL, "无法执行 %s" % PYTHON, out)
    elif ".".join(ver.split(".")[:2]) != "3.13":
        record("Python 版本为 3.13", FAIL, "当前 %s" % ver, out)
    else:
        record("Python 版本为 3.13", PASS, "Python %s" % ver)


def check_dependencies():
    """P4 新增依赖（openpyxl）已安装且可用。"""
    script = (
        "import openpyxl, django;"
        "print('openpyxl=' + openpyxl.__version__);"
        "print('django=' + django.get_version())"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("P4 依赖已安装", FAIL,
               "导入失败（是否忘了 pip install -r requirements.txt）", out)
        return
    first = out.strip().splitlines()[0] if out.strip() else ""
    if not first.startswith("openpyxl="):
        record("P4 依赖已安装", FAIL, "输出异常：%s" % first, out)
    else:
        record("P4 依赖已安装", PASS, ", ".join(
            l.strip() for l in out.strip().splitlines() if "=" in l))


def check_requirements_pinned():
    """openpyxl 版本已锁定。"""
    text = (BASE_DIR / "requirements.txt").read_text(encoding="utf-8")
    if not re.search(r"^openpyxl==", text, re.M):
        record("P4 依赖版本已锁定", FAIL, "openpyxl 未锁定版本（应使用 ==）")
    else:
        match = re.search(r"^openpyxl==(\S+)", text, re.M)
        record("P4 依赖版本已锁定", PASS, "openpyxl==%s" % match.group(1))


def check_django_checks():
    """Django 系统检查。"""
    code, out = run([PYTHON, "manage.py", "check"])
    if code != 0:
        record("Django 系统检查", FAIL, "manage.py check 未通过", out)
    elif "WARNINGS" in out or "ERRORS" in out:
        record("Django 系统检查", FAIL, "存在警告或错误", out)
    else:
        record("Django 系统检查", PASS, "no issues")


def check_migrations():
    """模型与迁移一致，且已全部应用。"""
    code, out = run([PYTHON, "manage.py", "makemigrations", "--check", "--dry-run"])
    if code != 0:
        record("模型与迁移一致", FAIL, "存在未生成的迁移", out)
        return
    code, out = run([PYTHON, "manage.py", "migrate", "--check"])
    if code != 0:
        record("模型与迁移一致", FAIL, "存在未应用的迁移", out)
        return
    record("模型与迁移一致", PASS, "无待生成、无待应用")


def check_database():
    """数据库为 PostgreSQL 18。"""
    script = (
        "import os,django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.db import connection;"
        "c=connection.cursor();c.execute('SHOW server_version');"
        "print('engine='+connection.settings_dict['ENGINE']);"
        "print('server='+str(c.fetchone()[0]))"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("数据库为 PostgreSQL 18", FAIL, "无法连接数据库", out)
        return
    engine = server = ""
    for line in out.splitlines():
        if line.startswith("engine="):
            engine = line.split("=", 1)[1].strip()
        if line.startswith("server="):
            server = line.split("=", 1)[1].strip()
    if "postgresql" not in engine:
        record("数据库为 PostgreSQL 18", FAIL, "ENGINE=%s（禁止 SQLite）" % engine, out)
    elif server.split(".")[0] != "18":
        record("数据库为 PostgreSQL 18", FAIL, "服务端版本 %s" % server, out)
    else:
        record("数据库为 PostgreSQL 18", PASS, "server=%s" % server)


# ===========================================================================
# P4 结构
# ===========================================================================

def check_app_registered():
    """reports 应用已注册，且既有配置未被改坏。"""
    script = (
        "import os,django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.conf import settings;"
        "print('reports' in settings.INSTALLED_APPS);"
        "print(settings.AUTH_USER_MODEL);"
        "print('submissions' in settings.INSTALLED_APPS);"
        "print('reviews' in settings.INSTALLED_APPS)"
    )
    code, out = run([PYTHON, "-c", script])
    lines = [l.strip() for l in out.strip().splitlines()]
    if code != 0 or len(lines) < 4:
        record("reports 应用已注册", FAIL, "无法读取配置", out)
        return
    if lines[0] != "True":
        record("reports 应用已注册", FAIL, "reports 未注册", out)
    elif lines[1] != "accounts.User":
        record("reports 应用已注册", FAIL, "用户模型被改动：%s" % lines[1], out)
    elif lines[2] != "True" or lines[3] != "True":
        record("reports 应用已注册", FAIL, "P3 应用被移除", out)
    else:
        record("reports 应用已注册", PASS,
               "reports 已注册；用户模型与 P3 应用保持不变")


def check_modules_exist():
    """P4 核心模块可导入。"""
    script = (
        "import os,django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from reports import filters, statistics, excel, views;"
        "print('ok')"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("P4 核心模块存在", FAIL, "模块导入失败", out)
    else:
        record("P4 核心模块存在", PASS,
               "filters（筛选）/ statistics（统计）/ excel（导出）/ views")


def check_counting_rule_live():
    """**核心口径实测**：一条记录多版本只计 1 条。

    这是 P4 最容易做错的地方，因此用真实数据独立复验
    （不依赖测试文件的写法 —— 若测试被误改，这里仍能拦住）。
    """
    script = r'''
import io, os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from accounts.models import Role
from competitions.models import Competition, CompetitionStatus, ReviewerAssignment
from tasks.models import CompetitionTask, TaskAssignment, TaskTemplate, WorkType
from submissions import orchestration, services
from submissions.models import Submission, SubmissionVersion, Attachment
from reports.filters import ReportFilters
from reports import statistics

U = get_user_model()
MARK = "VF4-"
problems = []

def cleanup():
    # 删除顺序须遵循 PROTECT 依赖链：引用方 → 被引用方
    # ReviewLog.version 也是 PROTECT，必须先删审核记录
    from reviews.models import ReviewLog
    ReviewLog.objects.filter(
        submission__assignment__member__student_id__startswith=MARK).delete()
    ReviewLog.objects.filter(reviewer__student_id__startswith=MARK).delete()
    Attachment.objects.filter(uploaded_by__student_id__startswith=MARK).delete()
    Attachment.objects.filter(
        version__submission__assignment__member__student_id__startswith=MARK).delete()
    SubmissionVersion.objects.filter(submitted_by__student_id__startswith=MARK).delete()
    SubmissionVersion.objects.filter(
        submission__assignment__member__student_id__startswith=MARK).delete()
    Submission.objects.filter(
        assignment__member__student_id__startswith=MARK).delete()
    ReviewerAssignment.objects.filter(competition__name__startswith="[VF4]").delete()
    TaskAssignment.objects.filter(member__student_id__startswith=MARK).delete()
    TaskAssignment.objects.filter(task__competition__name__startswith="[VF4]").delete()
    CompetitionTask.objects.filter(competition__name__startswith="[VF4]").delete()
    TaskTemplate.objects.filter(name__startswith="[VF4]").delete()
    Competition.objects.filter(name__startswith="[VF4]").delete()
    WorkType.objects.filter(name__startswith="[VF4]").delete()
    U.objects.filter(student_id__startswith=MARK).delete()

def jpg(c=(9,9,9)):
    im = Image.new("RGB",(800,600),c); b=io.BytesIO(); im.save(b,"JPEG"); return b.getvalue()
class FF:
    def __init__(self,d): self._d=d
    def getlist(self,k):
        v=self._d.get(k,[]); return v if isinstance(v,list) else [v]
    def get(self,k):
        v=self._d.get(k); return v[0] if isinstance(v,list) and v else v

def mk(sid,name,code=None):
    u=U.objects.create_user(student_id=sid,password="Vf4!Passw0rd9",real_name=name)
    u.must_change_password=False; u.save(update_fields=["must_change_password"])
    if code:
        r,_=Role.objects.get_or_create(code=code,defaults={"name":code}); u.roles.add(r)
    return u

cleanup()
try:
    admin = mk(MARK+"ADM","验证管理员","club_admin")
    m1 = mk(MARK+"M1","验证干事","member")
    wt = WorkType.objects.create(name="[VF4] 类型", created_by=admin)
    tpl = TaskTemplate.objects.create(name="[VF4] 模板", work_type=wt, created_by=admin,
        allow_text=True, require_text=True, allow_image=True, require_image=True,
        max_records_per_member=3)
    comp = Competition.objects.create(name="[VF4] 比赛", event_date=timezone.localdate(),
        default_deadline=timezone.now()+timedelta(days=3),
        status=CompetitionStatus.ACTIVE, created_by=admin)
    task = CompetitionTask.build_from_template(competition=comp, template=tpl, created_by=admin); task.save()
    asg = TaskAssignment.objects.create(task=task, member=m1, assigned_by=admin)
    ReviewerAssignment.objects.create(competition=comp, reviewer=admin, granted_by=admin)

    s = orchestration.submit_new(assignment_id=asg.pk, user=m1, text_content="初版",
        post_data={}, files=FF({"images":[SimpleUploadedFile("a.jpg",jpg(),content_type="image/jpeg")]}))
    for i in (1,2):
        with transaction.atomic():
            services.reject_submission(submission=services.lock_submission(s.pk),
                                       reviewer=admin, comment="退回%d"%i)
        orchestration.submit_revision(submission_id=s.pk, user=m1,
            text_content="第%d次修改"%(i+1), post_data={},
            files=FF({"images":[SimpleUploadedFile("a%d.jpg"%i,jpg((i*30,0,0)),content_type="image/jpeg")]}))

    versions = SubmissionVersion.objects.filter(submission=s).count()
    # 断言范围必须收敛到本次造的数据：按比赛筛选，
    # 避免把库里既有的历史数据一起统计进来（那会造成误报）。
    f = ReportFilters(competition_id=comp.pk)
    summary = statistics.summarize(admin, f)
    by_member = statistics.by_member(admin, f)
    by_type = statistics.by_work_type(admin, f)
    det = statistics.details(admin, f)

    if versions != 3: problems.append("版本数应为 3，实际 %d" % versions)
    if summary["total_records"] != 1: problems.append("记录数应为 1，实际 %d（把版本当记录计数了？）" % summary["total_records"])
    if summary["version_count"] != 3: problems.append("版本统计应为 3，实际 %d" % summary["version_count"])
    if summary["total_records"] == summary["version_count"]: problems.append("记录数与版本数相等，口径可疑")

    # 三表加总一致
    a = sum(r["total_records"] for r in by_member)
    b = sum(r["total_records"] for r in by_type)
    c = len(det)
    if not (a == b == c == summary["total_records"]):
        problems.append("三表加总不一致：人员%d 类型%d 明细%d 概览%d" % (a,b,c,summary["total_records"]))
    if len(det) != 1: problems.append("明细应为 1 行，实际 %d（多版本重复出行了？）" % len(det))
    if det and det[0]["version_count"] != 3: problems.append("明细版本数应为 3")

    # 导出侧也要只出现一次
    import io as _io
    from reports import excel
    from datetime import datetime
    report = statistics.build_report(admin, f)
    report["generated_at"] = datetime(2026,10,20,12,0)
    from openpyxl import load_workbook
    wb = load_workbook(_io.BytesIO(excel.render_xlsx_bytes(report)))
    ws = wb["工作记录明细"]
    hdr = None
    for r in range(1,15):
        if ws.cell(row=r,column=1).value == "记录编号":
            hdr = r; break
    rows = sum(1 for r in range(hdr+1, ws.max_row+1) if ws.cell(row=r,column=1).value)
    if rows != 1: problems.append("导出的明细行数应为 1，实际 %d" % rows)
finally:
    cleanup()

print("PROBLEMS:" + ("|".join(problems) if problems else "NONE"))
'''
    code, out = run([PYTHON, "-c", script], timeout=900)
    marker = [l for l in out.splitlines() if l.startswith("PROBLEMS:")]
    if code != 0 or not marker:
        record("核心口径：版本不重复计入（独立验证）", FAIL, "复验脚本失败", out)
        return
    payload = marker[-1].split("PROBLEMS:", 1)[1]
    if payload != "NONE":
        record("核心口径：版本不重复计入（独立验证）", FAIL,
               payload.replace("|", "；"), out)
    else:
        record("核心口径：版本不重复计入（独立验证）", PASS,
               "1 条记录 3 个版本 → 记录数=1、版本数=3；三表加总一致；导出明细仅 1 行")


def check_filter_isolation_live():
    """权限隔离实测：干事不能通过筛选参数拿到他人数据。"""
    script = r'''
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
import io
from PIL import Image

from accounts.models import Role
from competitions.models import Competition, CompetitionStatus, ReviewerAssignment
from tasks.models import CompetitionTask, TaskAssignment, TaskTemplate, WorkType
from submissions import orchestration
from submissions.models import Submission, SubmissionVersion, Attachment
from reports.filters import ReportFilters
from reports import statistics

U = get_user_model()
MARK = "VF4B-"
problems = []

def cleanup():
    # 删除顺序须遵循 PROTECT 依赖链：引用方 → 被引用方
    # ReviewLog.version 也是 PROTECT，必须先删审核记录
    from reviews.models import ReviewLog
    ReviewLog.objects.filter(
        submission__assignment__member__student_id__startswith=MARK).delete()
    ReviewLog.objects.filter(reviewer__student_id__startswith=MARK).delete()
    Attachment.objects.filter(uploaded_by__student_id__startswith=MARK).delete()
    Attachment.objects.filter(
        version__submission__assignment__member__student_id__startswith=MARK).delete()
    SubmissionVersion.objects.filter(submitted_by__student_id__startswith=MARK).delete()
    SubmissionVersion.objects.filter(
        submission__assignment__member__student_id__startswith=MARK).delete()
    Submission.objects.filter(assignment__member__student_id__startswith=MARK).delete()
    ReviewerAssignment.objects.filter(competition__name__startswith="[VF4B]").delete()
    TaskAssignment.objects.filter(member__student_id__startswith=MARK).delete()
    TaskAssignment.objects.filter(task__competition__name__startswith="[VF4B]").delete()
    CompetitionTask.objects.filter(competition__name__startswith="[VF4B]").delete()
    TaskTemplate.objects.filter(name__startswith="[VF4B]").delete()
    Competition.objects.filter(name__startswith="[VF4B]").delete()
    WorkType.objects.filter(name__startswith="[VF4B]").delete()
    U.objects.filter(student_id__startswith=MARK).delete()

def jpg():
    im=Image.new("RGB",(600,400),(5,5,5)); b=io.BytesIO(); im.save(b,"JPEG"); return b.getvalue()
class FF:
    def __init__(self,d): self._d=d
    def getlist(self,k):
        v=self._d.get(k,[]); return v if isinstance(v,list) else [v]
    def get(self,k):
        v=self._d.get(k); return v[0] if isinstance(v,list) and v else v
def mk(sid,name,code=None):
    u=U.objects.create_user(student_id=sid,password="Vf4b!Passw0rd9",real_name=name)
    u.must_change_password=False; u.save(update_fields=["must_change_password"])
    if code:
        r,_=Role.objects.get_or_create(code=code,defaults={"name":code}); u.roles.add(r)
    return u

cleanup()
try:
    admin = mk(MARK+"ADM","验证管理员","club_admin")
    a = mk(MARK+"A","干事A","member")
    b = mk(MARK+"B","干事B","member")
    wt = WorkType.objects.create(name="[VF4B] 类型", created_by=admin)
    tpl = TaskTemplate.objects.create(name="[VF4B] 模板", work_type=wt, created_by=admin,
        allow_text=True, require_text=True, allow_image=True, require_image=True,
        max_records_per_member=1)
    comp = Competition.objects.create(name="[VF4B] 比赛", event_date=timezone.localdate(),
        default_deadline=timezone.now()+timedelta(days=3),
        status=CompetitionStatus.ACTIVE, created_by=admin)
    task = CompetitionTask.build_from_template(competition=comp, template=tpl, created_by=admin); task.save()
    asg_a = TaskAssignment.objects.create(task=task, member=a, assigned_by=admin)
    asg_b = TaskAssignment.objects.create(task=task, member=b, assigned_by=admin)

    orchestration.submit_new(assignment_id=asg_a.pk, user=a, text_content="A的记录",
        post_data={}, files=FF({"images":[SimpleUploadedFile("a.jpg",jpg(),content_type="image/jpeg")]}))
    orchestration.submit_new(assignment_id=asg_b.pk, user=b, text_content="B的记录",
        post_data={}, files=FF({"images":[SimpleUploadedFile("b.jpg",jpg(),content_type="image/jpeg")]}))

    # A 看自己的 → 只有自己
    rows = statistics.by_member(a, ReportFilters())
    ids = {r["student_id"] for r in rows}
    if ids != {a.student_id}: problems.append("干事A 看到 %s，应只有自己" % ids)

    # A 显式筛 B → 不应拿到
    rows2 = statistics.by_member(a, ReportFilters(member_id=b.pk))
    if rows2: problems.append("干事A 用 member 参数筛 B 却拿到了 %d 行" % len(rows2))

    det_a = statistics.details(a, ReportFilters())
    if {r["member_student_id"] for r in det_a} - {a.student_id}:
        problems.append("干事A 的明细里出现了他人记录")

    # 管理员看全部
    rows3 = statistics.by_member(admin, ReportFilters())
    ids3 = {r["student_id"] for r in rows3}
    if not {a.student_id, b.student_id} <= ids3:
        problems.append("管理员应能看到全部，实际 %s" % ids3)
finally:
    cleanup()

print("PROBLEMS:" + ("|".join(problems) if problems else "NONE"))
'''
    code, out = run([PYTHON, "-c", script], timeout=900)
    marker = [l for l in out.splitlines() if l.startswith("PROBLEMS:")]
    if code != 0 or not marker:
        record("权限隔离（独立验证）", FAIL, "复验脚本失败", out)
        return
    payload = marker[-1].split("PROBLEMS:", 1)[1]
    if payload != "NONE":
        record("权限隔离（独立验证）", FAIL, payload.replace("|", "；"), out)
    else:
        record("权限隔离（独立验证）", PASS,
               "干事只见本人；显式筛他人不返回数据；管理员可见全部")


def check_routes():
    """P4 路由已挂载。"""
    routes = [("reports:page", []), ("reports:export", [])]
    script = (
        "import os,django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.urls import reverse;"
        "print(reverse('reports:page'));"
        "print(reverse('reports:export'))"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("P4 路由已挂载", FAIL, "URL 解析失败", out)
        return
    paths = out.strip().splitlines()
    if len(paths) < 2 or paths[0] != "/reports/" or paths[1] != "/reports/export/":
        record("P4 路由已挂载", FAIL, "路径不符：%s" % paths, out)
    else:
        record("P4 路由已挂载", PASS, "/reports/ 与 /reports/export/")


# ===========================================================================
# 测试执行
# ===========================================================================

def check_p4_tests():
    """P4 测试全部通过。"""
    code, out = run([PYTHON, "-m", "pytest", "-q", P4_TEST_DIR])
    passed, failed, errors, skipped = _counts(out)
    if code != 0 or failed or errors:
        record("P4 测试全部通过", FAIL,
               "passed=%d failed=%d errors=%d" % (passed, failed, errors), out)
    elif passed == 0:
        record("P4 测试全部通过", FAIL, "没有收集到 P4 测试（未执行≠通过）", out)
    elif skipped:
        record("P4 测试全部通过", FAIL, "存在被跳过的测试：%d 项" % skipped, out)
    else:
        record("P4 测试全部通过", PASS, _summary(out))


def _regression(name, files, min_expected):
    missing = [f for f in files if not (BASE_DIR / f).exists()]
    if missing:
        record(name, FAIL, "测试文件缺失：%s" % missing)
        return
    code, out = run([PYTHON, "-m", "pytest", "-q"] + files)
    passed, failed, errors, skipped = _counts(out)
    if code != 0 or failed or errors:
        record(name, FAIL,
               "passed=%d failed=%d errors=%d" % (passed, failed, errors), out)
    elif passed < min_expected:
        record(name, FAIL,
               "测试数从 %d 降到 %d（可能被删除或跳过）" % (min_expected, passed), out)
    else:
        record(name, PASS, _summary(out))


def check_p1_regression():
    """P1 测试未退化。"""
    _regression("P1 回归测试未退化", P1_TEST_FILES, 52)


def check_p2_regression():
    """P2 测试未退化。"""
    _regression("P2 回归测试未退化", P2_TEST_FILES, 141)


def check_p3_regression():
    """P3 测试未退化。"""
    _regression("P3 回归测试未退化", P3_TEST_FILES, 121)


def check_full_suite():
    """全套测试通过。"""
    code, out = run([PYTHON, "-m", "pytest", "-q"], timeout=2400)
    passed, failed, errors, _ = _counts(out)
    if code != 0 or failed or errors:
        record("全套测试通过", FAIL,
               "passed=%d failed=%d errors=%d" % (passed, failed, errors), out)
    else:
        record("全套测试通过", PASS, _summary(out))


def check_postgres_marker():
    """PostgreSQL 专项测试继续在真实 PostgreSQL 上运行。"""
    code, out = run([PYTHON, "-m", "pytest", "-q", "-m", "postgres"])
    passed, failed, errors, _ = _counts(out)
    if code != 0 or failed or errors:
        record("PostgreSQL 专项测试", FAIL,
               "passed=%d failed=%d errors=%d" % (passed, failed, errors), out)
    elif passed == 0:
        record("PostgreSQL 专项测试", FAIL, "没有匹配到 postgres 标记测试", out)
    else:
        record("PostgreSQL 专项测试", PASS, _summary(out))


# ===========================================================================
# 回归与完整性
# ===========================================================================

def check_previous_files_not_rewritten():
    """P1/P2/P3 既有文件未被覆盖。

    允许的改动（均为追加式）：
      · config/settings/base.py、config/urls.py —— 注册新应用与路由
      · templates/base.html —— 导航加入口
      · requirements.txt / README.md / .env.example / .gitignore
      · Dockerfile、docker-compose.yml、deploy/nginx/ —— 部署相关
      · config/settings/prod.py、core/audit.py、core/urls.py、core/views.py
        （P3 已登记，且 core/views.py 的 live/ready 逐字受函数级保护）
    """
    import ast
    import hashlib

    APPEND_OK = {
        "config/settings/base.py", "config/urls.py", "templates/base.html",
        "requirements.txt", "README.md", ".env.example", ".gitignore",
        "Dockerfile", "docker-compose.yml", "deploy/nginx/pingpong.conf",
        "config/settings/prod.py", "core/audit.py", "core/urls.py",
    }
    FUNC_GUARDED = {"core/views.py": ["live", "ready"]}

    # P5 登记的有意改动（非追加式，逐项给出理由；不在列表内的改动仍判失败）。
    # 之所以要显式登记而不是放宽比较：这两处是「规则变更」导致的必要改写，
    # 若静默放行，后续阶段就可能借这条通道悄悄改掉验证逻辑。
    P5_CHANGED = {
        "core/storage.py":
            "P5 规则变更：生产存储由强制 COS 改为服务器本地磁盘。"
            "build_storage() 改为按 PRIVATE_MEDIA_ROOT 是否仍为开发默认值"
            "来 fail-closed（容器内即临时层，重建会丢附件）。"
            "安全不变量（签名链接、路径穿越防护）未变，另有专项复核。",
        "scripts/verify_stage3.py":
            "P3 验收入口内有三处断言基于旧规则（生产强制 cos），P5 下前提失效，"
            "按新规则改写为「生产用默认数据目录 / 无目录 / COS 凭据不全 / 未知后端」"
            "四种场景均须报错，并把越界检查改为针对 P3 阶段末提交树"
            "（否则 P4 合法新增的 reports/ 会被误判为 P3 越界）。",
    }

    baseline = BASE_DIR / "scripts" / "p4_baseline.json"
    if not baseline.exists():
        record("P1/P2/P3 既有文件未被覆盖", SKIP, "未找到 P4 基线文件")
        return

    data = json.loads(baseline.read_text(encoding="utf-8"))
    changed, missing = [], []
    for rel, expect in data["files"].items():
        if rel in APPEND_OK or rel in FUNC_GUARDED or rel in P5_CHANGED:
            continue
        path = BASE_DIR / rel.replace("/", os.sep)
        if not path.exists():
            missing.append(rel)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expect:
            changed.append(rel)

    if missing or changed:
        detail = []
        if missing:
            detail.append("文件缺失：%s" % missing)
        if changed:
            detail.append("内容被改动：%s" % changed)
        record("P1/P2/P3 既有文件未被覆盖", FAIL, "；".join(detail))
        return

    notes = []
    for rel, funcs in FUNC_GUARDED.items():
        try:
            old = subprocess.run(["git", "show", "HEAD:%s" % rel],
                                 cwd=str(BASE_DIR), capture_output=True,
                                 encoding="utf-8", errors="replace", timeout=60).stdout
        except Exception as exc:  # noqa: BLE001
            record("P1/P2/P3 既有文件未被覆盖", FAIL, "无法读取 %s：%r" % (rel, exc))
            return
        if not old.strip():
            record("P1/P2/P3 既有文件未被覆盖", FAIL, "git 中找不到 %s" % rel)
            return

        def dump(text, names):
            tree = ast.parse(text)
            return {n.name: ast.dump(n) for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name in names}

        path = BASE_DIR / rel.replace("/", os.sep)
        o, n = dump(old, funcs), dump(path.read_text(encoding="utf-8"), funcs)
        bad = [f for f in funcs if o.get(f) != n.get(f)]
        if bad:
            record("P1/P2/P3 既有文件未被覆盖", FAIL,
                   "%s 中的安全关键函数被改动：%s" % (rel, bad))
            return
        notes.append("%s 的 %s 逐字未变" % (rel, "/".join(funcs)))

    # P5 登记改动必须逐项复核安全不变量：登记本身不是免检通行证
    storage_path = BASE_DIR / "core" / "storage.py"
    if storage_path.exists():
        st_src = storage_path.read_text(encoding="utf-8")
        st_tree = ast.parse(st_src)
        funcs_by_name = {n.name: n for n in st_tree.body
                         if isinstance(n, ast.FunctionDef)}
        missing_invariants = []
        # 1) 签名下载仍需 SALT + 过期校验
        if "DOWNLOAD_SALT" not in st_src:
            missing_invariants.append("DOWNLOAD_SALT 缺失")
        verify_fn = funcs_by_name.get("verify_download_token")
        if verify_fn is None:
            missing_invariants.append("verify_download_token 缺失")
        else:
            vsrc = ast.get_source_segment(st_src, verify_fn) or ""
            if "SignatureExpired" not in vsrc:
                missing_invariants.append("签名过期未校验")
            if "max_age" not in vsrc and "expires" not in vsrc:
                missing_invariants.append("有效期未校验")
        # 2) 对象键必须服务端生成（不接受用户输入拼路径）
        if "generate_object_key" not in funcs_by_name:
            missing_invariants.append("generate_object_key 缺失")
        # 3) 路径穿越防护仍在本地实现中
        local_cls = next((n for n in st_tree.body
                          if isinstance(n, ast.ClassDef)
                          and n.name == "LocalPrivateStorage"), None)
        if local_cls is None:
            missing_invariants.append("LocalPrivateStorage 缺失")
        else:
            lsrc = ast.get_source_segment(st_src, local_cls) or ""
            if not ("StorageConfigError" in lsrc and (".." in lsrc or "resolve" in lsrc)):
                missing_invariants.append("路径穿越防护疑似丢失")
        # 4) 生产 fail-closed 仍存在
        bs_fn = funcs_by_name.get("build_storage")
        if bs_fn is None:
            missing_invariants.append("build_storage 缺失")
        else:
            bsrc = ast.get_source_segment(st_src, bs_fn) or ""
            if "is_production" not in bsrc or "StorageConfigError" not in bsrc:
                missing_invariants.append("build_storage 的 fail-closed 校验疑似丢失")

        if missing_invariants:
            record("P1/P2/P3 既有文件未被覆盖", FAIL,
                   "P5 登记改动的安全不变量被破坏：%s" % "、".join(missing_invariants))
            return
        notes.append("storage.py 安全不变量（签名过期/服务端生成键/路径穿越/fail-closed）复核通过")

    plain = len(data["files"]) - len(APPEND_OK) - len(FUNC_GUARDED) - len(P5_CHANGED)
    record("P1/P2/P3 既有文件未被覆盖", PASS,
           "%d 个文件哈希一致 + %s（P5 登记改动 %d 处，已注明理由并复核不变量）"
           % (plain, "；".join(notes), len(P5_CHANGED)))


def check_agent_loop_untouched():
    """工作区 agent-loop 护栏字节级不变。"""
    import hashlib

    guard = (BASE_DIR.parent / "化学" / "agent-loop" / "probe" / "evidence"
             / "p1_guard_baseline.json")
    if not guard.exists():
        record("agent-loop 护栏未被修改", SKIP, "未找到护栏基线")
        return
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
               "%d 个护栏文件哈希一致" % len(data["files"]))


def check_scope_no_points():
    """P4 只做统计与报表，**未实现贡献积分**（任务明确要求）。"""
    forbidden_dirs = ["points", "scoring"]
    found = [d for d in forbidden_dirs if (BASE_DIR / d).exists()]
    if found:
        record("未越界实现贡献积分", FAIL, "发现越界模块：%s" % found)
        return

    # 排除本脚本自身（它包含用于检测的关键字）
    code, out = run(["git", "grep", "-nE",
                     r"(contribution_points|积分规则|points_balance)",
                     "--", "*.py", ":!scripts/verify_stage4.py", ":!scripts/verify_stage3.py"])
    hits = [l for l in out.splitlines() if l.strip()]
    if hits:
        record("未越界实现贡献积分", FAIL, "发现积分相关实现：%s" % hits[:3])
    else:
        record("未越界实现贡献积分", PASS, "无 points/scoring 模块与积分实现")


def check_readme_consistency():
    """README 记录了 P4 内容。"""
    readme = BASE_DIR / "README.md"
    if not readme.exists():
        record("README 与项目一致", FAIL, "缺少 README.md")
        return
    text = readme.read_text(encoding="utf-8").replace("\\", "/")
    required = ["scripts/verify_stage4.py", "reports", "/reports/",
                "按人员汇总", "按工作类型汇总", "工作记录明细"]
    missing = [r for r in required if r not in text]
    if not (BASE_DIR / "scripts" / "verify_stage4.py").exists():
        missing.append("scripts/verify_stage4.py(磁盘缺失)")
    if missing:
        record("README 与项目一致", FAIL, "缺少：%s" % missing)
    else:
        record("README 与项目一致", PASS, "P4 页面路径、三张表与验收入口均已记录")


def check_deploy_readiness():
    """P5：部署配置自检通过（Dockerfile 应用清单 / 挂载 / 密钥卫生 / 文档）。

    单独跑 scripts/verify_deploy.py，把它作为 P4 验收流水线的一环，
    避免"改完部署配置忘了验"。
    """
    script = BASE_DIR / "scripts" / "verify_deploy.py"
    if not script.exists():
        record("部署配置自检", FAIL, "缺少 scripts/verify_deploy.py")
        return
    code, out = run([PYTHON, str(script)], timeout=300)
    failed = [l for l in out.splitlines() if l.strip().startswith("❌")]
    if code != 0 or failed:
        detail = "；".join(l.strip().lstrip("❌").strip() for l in failed[:3]) \
            or "自检脚本退出码 %d" % code
        record("部署配置自检", FAIL, detail, out)
        return
    summary = [l for l in out.splitlines() if l.startswith("汇总")]
    record("部署配置自检", PASS,
           summary[0] if summary else "全部通过（Dockerfile/挂载/密钥/文档）")


# ===========================================================================
# 主流程
# ===========================================================================

CHECKS = [
    ("python", check_python),
    ("deps", check_dependencies),
    ("pin", check_requirements_pinned),
    ("check", check_django_checks),
    ("migrate", check_migrations),
    ("db", check_database),
    ("apps", check_app_registered),
    ("modules", check_modules_exist),
    ("routes", check_routes),
    ("counting", check_counting_rule_live),
    ("isolation", check_filter_isolation_live),
    ("p4tests", check_p4_tests),
    ("p1reg", check_p1_regression),
    ("p2reg", check_p2_regression),
    ("p3reg", check_p3_regression),
    ("pgmark", check_postgres_marker),
    ("full", check_full_suite),
    ("prevfile", check_previous_files_not_rewritten),
    ("guard", check_agent_loop_untouched),
    ("scope", check_scope_no_points),
    ("readme", check_readme_consistency),
    ("deploy", check_deploy_readiness),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="P4 阶段统一验收入口")
    ap.add_argument("--only", help="只跑指定检查（逗号分隔的 key）")
    ap.add_argument("--list", action="store_true", help="列出所有检查项")
    args = ap.parse_args()

    if args.list:
        for key, fn in CHECKS:
            doc = (fn.__doc__ or "").splitlines()
            print("%-10s %s" % (key, doc[0] if doc else ""))
        return 0

    only = set(args.only.split(",")) if args.only else None

    print("=" * 78)
    print("P4 阶段验证 —— 统计与 Excel 报表")
    print("项目目录：%s" % BASE_DIR)
    print("解释器  ：%s" % PYTHON)
    print("=" * 78)

    started = time.time()
    for key, fn in CHECKS:
        if only and key not in only:
            continue
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            record((fn.__doc__ or key).splitlines()[0] or key, FAIL,
                   "检查抛出异常：%r" % exc)

    failures = [r for r in results if r["status"] == FAIL]
    skips = [r for r in results if r["status"] == SKIP]
    passes = [r for r in results if r["status"] == PASS]

    print()
    print("=" * 78)
    print("汇总：通过 %d 项，失败 %d 项，跳过（环境阻塞）%d 项，耗时 %.1fs"
          % (len(passes), len(failures), len(skips), time.time() - started))
    if skips:
        print("-" * 78)
        print("未验证事项（环境阻塞，**不代表通过**）：")
        for r in skips:
            print("  · %s —— %s" % (r["name"], r["detail"]))
    if failures:
        print("-" * 78)
        print("失败项：")
        for r in failures:
            print("  ✗ %s —— %s" % (r["name"], r["detail"]))
    print("=" * 78)

    report = BASE_DIR / "verify_stage4_report.json"
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
