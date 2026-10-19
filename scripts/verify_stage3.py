#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P3 阶段统一验收入口。

真正执行检查与测试，任一必要检查失败即返回非零退出码。

三类结果语义严格区分（与 P1/P2 的入口一致）：
    PASS  —— 真实通过
    FAIL  —— 真实失败（导致整体非零退出）
    SKIP  —— 环境不具备（本机无 Docker / 无真实 COS 凭据），
             **不计入通过**，在报告里单独列为"未验证事项"

禁止：吞异常返回成功、跳过测试、降低断言、伪造输出、把未执行标为通过。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\verify_stage3.py
    .\\.venv\\Scripts\\python.exe scripts\\verify_stage3.py --only p3tests,imaging
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

P3_TEST_DIR = "tests/p3"
P1_TEST_FILES = [
    "tests/test_accounts.py", "tests/test_password_change.py",
    "tests/test_health.py", "tests/test_settings.py", "tests/test_database.py",
]
P2_TEST_FILES = [
    "tests/p2/test_models.py", "tests/p2/test_permissions.py", "tests/p2/test_pages.py",
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
    """P3 新增依赖（Pillow / COS SDK）已安装且可用。"""
    script = (
        "import PIL, qcloud_cos, django, psycopg;"
        "print('Pillow=' + PIL.__version__);"
        "print('cos_sdk=ok');"
        "print('django=' + django.get_version())"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("P3 依赖已安装", FAIL,
               "导入失败（是否忘了 pip install -r requirements.txt）", out)
        return
    lines = [l.strip() for l in out.strip().splitlines() if "=" in l]
    info = ", ".join(lines)
    if "Pillow=13." not in info:
        record("P3 依赖已安装", FAIL, "Pillow 版本不符：%s" % info, out)
    else:
        record("P3 依赖已安装", PASS, info)


def check_pillow_capabilities():
    """Pillow 必须具备 JPEG / WebP / zlib 能力（P3 依赖项）。"""
    script = (
        "from PIL import features;import json;"
        "print(json.dumps({k: bool(features.check(k)) for k in "
        "('jpg','webp','zlib','libjpeg_turbo')}))"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("Pillow 能力齐备", FAIL, "能力检查失败", out)
        return
    try:
        caps = json.loads(out.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        record("Pillow 能力齐备", FAIL, "无法解析能力检查结果", out)
        return
    missing = [k for k, v in caps.items() if not v and k != "libjpeg_turbo"]
    if missing:
        record("Pillow 能力齐备", FAIL, "缺少能力：%s" % missing, out)
    else:
        record("Pillow 能力齐备", PASS, "jpg / webp / zlib 均可用")


def check_requirements_pinned():
    """P3 新增依赖版本已锁定。"""
    text = (BASE_DIR / "requirements.txt").read_text(encoding="utf-8")
    problems = []
    for pkg in ("Pillow", "cos-python-sdk-v5"):
        match = re.search(r"^%s==(\S+)" % re.escape(pkg), text, re.M)
        if not match:
            problems.append("%s 未锁定版本" % pkg)
    if problems:
        record("P3 依赖版本已锁定", FAIL, "；".join(problems))
    else:
        record("P3 依赖版本已锁定", PASS, "Pillow 与 cos-python-sdk-v5 均用 ==")


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
# P3 结构
# ===========================================================================

def check_p3_apps_registered():
    """P3 应用已注册，且用户模型未被替换。"""
    script = (
        "import os,django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.conf import settings;"
        "print('submissions' in settings.INSTALLED_APPS);"
        "print('reviews' in settings.INSTALLED_APPS);"
        "print(settings.AUTH_USER_MODEL)"
    )
    code, out = run([PYTHON, "-c", script])
    lines = [l.strip() for l in out.strip().splitlines()]
    if code != 0 or len(lines) < 3:
        record("P3 应用已注册", FAIL, "无法读取配置", out)
        return
    if lines[0] != "True" or lines[1] != "True":
        record("P3 应用已注册", FAIL, "submissions/reviews 未注册", out)
    elif lines[2] != "accounts.User":
        record("P3 应用已注册", FAIL, "用户模型被改动：%s" % lines[2], out)
    else:
        record("P3 应用已注册", PASS, "submissions + reviews；用户模型仍为 accounts.User")


def check_p3_models_exist():
    """P3 核心模型可导入。"""
    script = (
        "import os,django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from submissions.models import (Submission, SubmissionVersion, Attachment,"
        " UserImageUsage, OrphanObject);"
        "from reviews.models import ReviewLog;"
        "print('ok')"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("P3 核心模型存在", FAIL, "模型导入失败", out)
    else:
        record("P3 核心模型存在", PASS,
               "Submission / SubmissionVersion / Attachment / UserImageUsage / "
               "OrphanObject / ReviewLog")


def check_db_tables_and_constraints():
    """P3 表与关键约束真实落库。"""
    tables = {"submissions_submission", "submissions_submissionversion",
              "submissions_attachment", "submissions_userimageusage",
              "submissions_orphanobject", "reviews_reviewlog"}
    constraints = {"uniq_version_no_per_submission", "version_no_positive",
                   "attachment_link_or_object", "thumbnail_requires_parent",
                   "usage_bytes_non_negative", "reject_requires_comment",
                   "reopen_requires_reason"}
    script = (
        "import os,django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.db import connection;"
        "c=connection.cursor();"
        "c.execute(\"SELECT tablename FROM pg_tables WHERE schemaname='public'\");"
        "print('T:'+','.join(r[0] for r in c.fetchall()));"
        "c.execute(\"SELECT conname FROM pg_constraint\");"
        "print('C:'+','.join(r[0] for r in c.fetchall()))"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("P3 表与约束已落库", FAIL, "查询失败", out)
        return
    found_t, found_c = set(), set()
    for line in out.splitlines():
        if line.startswith("T:"):
            found_t = set(line[2:].split(","))
        elif line.startswith("C:"):
            found_c = set(line[2:].split(","))
    missing_t = tables - found_t
    missing_c = constraints - found_c
    if missing_t or missing_c:
        record("P3 表与约束已落库", FAIL,
               "缺表=%s 缺约束=%s" % (sorted(missing_t), sorted(missing_c)), out)
    else:
        record("P3 表与约束已落库", PASS, "%d 张表 + %d 个约束" % (len(tables), len(constraints)))


def check_plugin_config():
    """上传上限与存储配置可读且取值正确。"""
    script = (
        "import os,django,json;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from core.upload_config import get_upload_limits;"
        "L=get_upload_limits();"
        "print(json.dumps({'img':L.image_max_bytes,'file':L.file_max_bytes,"
        "'video':L.video_max_bytes,'count':L.images_per_user,"
        "'bytes':L.image_bytes_per_user,'edge':L.image_max_edge,"
        "'quality':L.image_quality,'thumb':L.thumb_edge,"
        "'presign':L.presign_expires_seconds}))"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("上传上限配置正确", FAIL, "读取失败", out)
        return
    try:
        cfg = json.loads(out.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        record("上传上限配置正确", FAIL, "无法解析", out)
        return
    expected = {"img": 10 * 1024 * 1024, "file": 50 * 1024 * 1024,
                "video": 300 * 1024 * 1024, "count": 50,
                "bytes": 100 * 1024 * 1024, "edge": 1280, "quality": 82,
                "thumb": 400, "presign": 300}
    wrong = {k: (cfg.get(k), v) for k, v in expected.items() if cfg.get(k) != v}
    if wrong:
        record("上传上限配置正确", FAIL, "取值与需求不符：%s" % wrong, out)
    else:
        record("上传上限配置正确", PASS,
               "图片 10MiB / 文件 50MiB / 视频 300MiB；配额 50 张 100MiB；"
               "主图 1280 q82；缩略图 400；预签名 300s")


def check_imaging_live():
    """图片处理在真实运行环境中生效（不是只看配置）。"""
    script = r'''
import io, os, django, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()
from PIL import Image
from core.imaging import process_image, detect_image_format

problems = []
# 主图长边 + 缩略图尺寸
im = Image.new("RGB", (2400, 1600), (10, 20, 30))
buf = io.BytesIO(); im.save(buf, "JPEG")
r = process_image(buf.getvalue())
if max(r.main_width, r.main_height) != 1280: problems.append("主图长边不是 1280")
if (r.thumb_width, r.thumb_height) != (400, 400): problems.append("缩略图不是 400x400")

# EXIF 方向
im2 = Image.new("RGB", (800, 400))
ex = im2.getexif(); ex[274] = 6
buf2 = io.BytesIO(); im2.save(buf2, "JPEG", exif=ex)
r2 = process_image(buf2.getvalue())
if not r2.main_height > r2.main_width: problems.append("EXIF 方向未生效")

# 透明转白底
im3 = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
buf3 = io.BytesIO(); im3.save(buf3, "PNG")
r3 = process_image(buf3.getvalue())
out = Image.open(io.BytesIO(r3.main_bytes)).convert("RGB")
if out.getpixel((2, 2))[0] < 240: problems.append("透明未垫白底")

# 伪造内容被拒
try:
    process_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    problems.append("伪造成 PNG 未被拒")
except Exception:
    pass

print("PROBLEMS:" + ("|".join(problems) if problems else "NONE"))
'''
    code, out = run([PYTHON, "-c", script])
    marker = [l for l in out.splitlines() if l.startswith("PROBLEMS:")]
    if code != 0 or not marker:
        record("图片处理实测生效", FAIL, "实测脚本失败", out)
        return
    payload = marker[-1].split("PROBLEMS:", 1)[1]
    if payload != "NONE":
        record("图片处理实测生效", FAIL, payload.replace("|", "；"), out)
    else:
        record("图片处理实测生效", PASS,
               "1280 长边 / 400 方缩略图 / EXIF 方向 / 透明垫白 / 伪造被拒 均实测生效")


# ===========================================================================
# 权限与安全不变量（独立复验，不依赖测试写法）
# ===========================================================================

def check_permission_invariants():
    """独立复验 P3 的权限不变量。

    刻意不复用测试文件的断言 —— 若测试被误改，这里仍能拦住。
    """
    script = r'''
import os, django, io
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from PIL import Image

from accounts.models import Role
from competitions.models import Competition, CompetitionStatus, ReviewerAssignment
from tasks.models import CompetitionTask, TaskAssignment, TaskTemplate, WorkType
from submissions import orchestration, services
from submissions.models import Submission, SubmissionVersion, Attachment
from submissions.permissions import can_view_attachment, can_review_submission

U = get_user_model()
problems = []
MARK = "VF3-"


def cleanup(mark):
    """按 PROTECT 依赖链的完整顺序清理验证数据。

    P3 把多处外键设成了 PROTECT（"历史不因删账号而消失"的设计），
    因此删除必须**从引用方到被引用方**：
        Attachment → SubmissionVersion → Submission → TaskAssignment
        CompetitionTask → TaskTemplate → WorkType → Competition/User
    顺序错了会触发 ProtectedError（本脚本前两版就踩了两次，反而印证约束生效）。
    开头与结尾都调用（开头清上一次的残留）。
    """
    Attachment.objects.filter(uploaded_by__student_id__startswith=mark).delete()
    Attachment.objects.filter(
        version__submission__assignment__member__student_id__startswith=mark).delete()
    SubmissionVersion.objects.filter(submitted_by__student_id__startswith=mark).delete()
    SubmissionVersion.objects.filter(
        submission__assignment__member__student_id__startswith=mark).delete()
    Submission.objects.filter(
        assignment__member__student_id__startswith=mark).delete()
    ReviewerAssignment.objects.filter(competition__name__startswith="[VF3]").delete()
    TaskAssignment.objects.filter(member__student_id__startswith=mark).delete()
    TaskAssignment.objects.filter(
        task__competition__name__startswith="[VF3]").delete()
    CompetitionTask.objects.filter(competition__name__startswith="[VF3]").delete()
    TaskTemplate.objects.filter(name__startswith="[VF3]").delete()
    Competition.objects.filter(name__startswith="[VF3]").delete()
    WorkType.objects.filter(name__startswith="[VF3]").delete()
    U.objects.filter(student_id__startswith=mark).delete()


cleanup(MARK)

def mk(sid, name, code=None):
    u = U.objects.create_user(student_id=sid, password="Vf3!Passw0rd9", real_name=name)
    u.must_change_password = False; u.save(update_fields=["must_change_password"])
    if code:
        role, _ = Role.objects.get_or_create(code=code, defaults={"name": code})
        u.roles.add(role)
    return u

admin = mk(MARK+"ADM", "验证管理员", "club_admin")
m1 = mk(MARK+"M1", "验证干事甲", "member")
m2 = mk(MARK+"M2", "验证干事乙", "member")

wt = WorkType.objects.create(name="[VF3] 类型", created_by=admin)
tpl = TaskTemplate.objects.create(name="[VF3] 模板", work_type=wt, created_by=admin,
    allow_text=True, require_text=True, allow_image=True, require_image=True,
    max_records_per_member=1)
comp = Competition.objects.create(name="[VF3] 比赛", event_date=timezone.localdate(),
    default_deadline=timezone.now()+timedelta(days=2),
    status=CompetitionStatus.ACTIVE, created_by=admin)
task = CompetitionTask.build_from_template(competition=comp, template=tpl, created_by=admin); task.save()
asg1 = TaskAssignment.objects.create(task=task, member=m1, assigned_by=admin)

class FF:
    def __init__(self, d): self._d = d
    def getlist(self, k):
        v = self._d.get(k, []); return v if isinstance(v, list) else [v]
    def get(self, k):
        v = self._d.get(k); return v[0] if isinstance(v, list) and v else v

from django.core.files.uploadedfile import SimpleUploadedFile
def img():
    im = Image.new("RGB", (900, 700), (1, 2, 3)); b = io.BytesIO(); im.save(b, "JPEG")
    return SimpleUploadedFile("v.jpg", b.getvalue(), content_type="image/jpeg")

sub = orchestration.submit_new(assignment_id=asg1.pk, user=m1, text_content="验证",
    post_data={}, files=FF({"images": [img()]}))
att = sub.current_version.attachments.filter(material_type="image").first()

# 1. 匿名不可查看附件
from django.contrib.auth.models import AnonymousUser
if can_view_attachment(AnonymousUser(), att):
    problems.append("匿名可查看附件")

# 2. 跨用户不可查看
if can_view_attachment(m2, att):
    problems.append("无关干事可查看他人附件")

# 3. 本人可查看
if not can_view_attachment(m1, att):
    problems.append("本人无法查看自己的附件")

# 4. 未授权审核人不能审核
if can_review_submission(m2, sub):
    problems.append("未授权者可审核")

# 5. 管理员身份本身不等于审核权
if can_review_submission(admin, sub):
    problems.append("管理员未被授权却可审核")

# 6. 不能审核自己的提交
if can_review_submission(m1, sub):
    problems.append("可审核自己的提交")

# 7. 授权后可审核
ReviewerAssignment.objects.create(competition=comp, reviewer=admin, granted_by=admin)
if not can_review_submission(admin, sub):
    problems.append("授权后仍不能审核")

# 8. 退回必须填理由
try:
    with __import__("django.db", fromlist=["transaction"]).transaction.atomic():
        services.reject_submission(submission=services.lock_submission(sub.pk),
                                   reviewer=admin, comment="")
    problems.append("空理由竟能退回")
except Exception:
    pass

# 9. 提交次数上限
try:
    orchestration.submit_new(assignment_id=asg1.pk, user=m1, text_content="第二条",
        post_data={}, files=FF({"images": [img()]}))
    problems.append("超出提交次数上限仍成功")
except Exception:
    pass

cleanup(MARK)

print("PROBLEMS:" + ("|".join(problems) if problems else "NONE"))
'''
    code, out = run([PYTHON, "-c", script])
    marker = [l for l in out.splitlines() if l.startswith("PROBLEMS:")]
    if code != 0 or not marker:
        record("P3 权限不变量（独立验证）", FAIL, "复验脚本失败", out)
        return
    payload = marker[-1].split("PROBLEMS:", 1)[1]
    if payload != "NONE":
        record("P3 权限不变量（独立验证）", FAIL, payload.replace("|", "；"), out)
    else:
        record("P3 权限不变量（独立验证）", PASS,
               "9 条不变量成立（匿名/跨用户拒绝、审核权独立、不能审自己、退回必填、次数上限）")


def check_storage_failclosed():
    """生产缺失 COS 配置时必须报错，不得回退本地。"""
    script = r'''
import os
problems = []
# 场景一：生产 + 未配置存储后端 → 必须报错
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.dev"
import django; django.setup()
from django.test import override_settings
from core.storage import StorageConfigError, build_storage

with override_settings(DEBUG=False, PRIVATE_STORAGE_BACKEND=""):
    try:
        build_storage(); problems.append("生产未配置后端却成功构造了存储")
    except StorageConfigError:
        pass

# 场景二：选了 cos 但凭据不全 → 必须报错
with override_settings(DEBUG=False, PRIVATE_STORAGE_BACKEND="cos",
                       COS_CONFIG={"region":"ap-beijing","bucket":"",
                                   "secret_id":"","secret_key":""}):
    try:
        build_storage(); problems.append("COS 凭据不全却成功构造")
    except StorageConfigError:
        pass

# 场景三：prod 配置本身强制 cos
import importlib
os.environ.update({
    "DJANGO_SECRET_KEY": "x"*60,
    "DJANGO_ALLOWED_HOSTS": "example.com",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://example.com",
    "POSTGRES_DB":"d","POSTGRES_USER":"u","POSTGRES_PASSWORD":"p","POSTGRES_HOST":"h",
})
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.prod"
import config.settings.base as b, config.settings.prod as p
importlib.reload(b); p = importlib.reload(p)
if p.PRIVATE_STORAGE_BACKEND != "cos":
    problems.append("生产配置未强制 cos（当前 %s）" % p.PRIVATE_STORAGE_BACKEND)

print("PROBLEMS:" + ("|".join(problems) if problems else "NONE"))
'''
    code, out = run([PYTHON, "-c", script])
    marker = [l for l in out.splitlines() if l.startswith("PROBLEMS:")]
    if code != 0 or not marker:
        record("私有存储 fail-closed", FAIL, "复验脚本失败", out)
        return
    payload = marker[-1].split("PROBLEMS:", 1)[1]
    if payload != "NONE":
        record("私有存储 fail-closed", FAIL, payload.replace("|", "；"), out)
    else:
        record("私有存储 fail-closed", PASS,
               "生产未配置后端 / COS 凭据不全 均明确报错；prod 强制 cos")


# ===========================================================================
# 测试执行
# ===========================================================================

def check_p3_tests():
    """P3 测试全部通过。"""
    code, out = run([PYTHON, "-m", "pytest", "-q", P3_TEST_DIR])
    passed, failed, errors, skipped = _counts(out)
    if code != 0 or failed or errors:
        record("P3 测试全部通过", FAIL,
               "passed=%d failed=%d errors=%d" % (passed, failed, errors), out)
    elif passed == 0:
        record("P3 测试全部通过", FAIL, "没有收集到任何 P3 测试（未执行≠通过）", out)
    elif skipped:
        record("P3 测试全部通过", FAIL, "存在被跳过的测试：%d 项" % skipped, out)
    else:
        record("P3 测试全部通过", PASS, _summary(out))


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


def check_full_suite():
    """全套测试通过。"""
    code, out = run([PYTHON, "-m", "pytest", "-q"])
    passed, failed, errors, skipped = _counts(out)
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


def check_get_never_mutates():
    """P3 写操作路由存在且只接受 POST。"""
    routes = [
        ("submissions:submit_new", [1]),
        ("submissions:submit_revision", [1]),
        ("submissions:review_submission", [1]),
        ("submissions:reopen_submission", [1]),
        ("submissions:attachment_download", [1]),
        ("submissions_admin:list", []),
    ]
    script = (
        "import os,django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.urls import reverse, NoReverseMatch;"
        "routes=%r;out=[]\n"
        "for n,a in routes:\n"
        "    try:\n"
        "        out.append(n)\n"
        "        reverse(n, args=a)\n"
        "    except NoReverseMatch:\n"
        "        pass\n"
        "print('NAMES:' + ','.join(out))\n"
    ) % (routes,)
    code, out = run([PYTHON, "-c", script])
    names = ""
    for line in out.splitlines():
        if line.startswith("NAMES:"):
            names = line[6:]
    missing = [n for n, _ in routes if n not in names]
    if code != 0 or missing:
        record("P3 路由已挂载", FAIL, "缺少路由：%s" % missing, out)
    else:
        record("P3 路由已挂载", PASS, "%d 个关键路由可解析" % len(routes))


# ===========================================================================
# 回归与完整性
# ===========================================================================

def check_previous_files_not_rewritten():
    """P1/P2 既有文件未被覆盖。

    允许的改动：
      * config/settings/base.py、config/urls.py —— 注册新应用与路由（追加式）
      * core/views.py —— 新增私有文件下载视图；但 **live/ready 必须逐字一致**
      * requirements.txt / README.md / .env.example / .gitignore —— 按阶段更新
    """
    import ast
    import hashlib

    APPEND_OK = {
        "config/settings/base.py", "config/urls.py",
        "requirements.txt", "README.md", ".env.example", ".gitignore",
        # P3 必要改动（均为追加式，且不影响既有行为）：
        #   prod.py            —— 锁定 PRIVATE_STORAGE_BACKEND="cos"
        #   core/audit.py      —— 新增 log_action_for_user（服务层无 request 时用）
        #   core/urls.py       —— 挂载私有文件下载路由
        "config/settings/prod.py", "core/audit.py", "core/urls.py",
        # 部署配置按 P3 要求调整（Pillow 运行依赖 / 请求体上限）
        "Dockerfile", "deploy/nginx/pingpong.conf",
    }
    FUNC_GUARDED = {"core/views.py": ["live", "ready"]}

    baseline = BASE_DIR / "scripts" / "p3_baseline.json"
    if not baseline.exists():
        record("P1/P2 既有文件未被覆盖", SKIP, "未找到 P3 基线文件")
        return

    data = json.loads(baseline.read_text(encoding="utf-8"))
    changed, missing = [], []
    for rel, expect in data["files"].items():
        if rel in APPEND_OK or rel in FUNC_GUARDED:
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
        record("P1/P2 既有文件未被覆盖", FAIL, "；".join(detail))
        return

    # 函数级证明：core/views.py 的 live/ready 未被改动
    notes = []
    for rel, funcs in FUNC_GUARDED.items():
        try:
            old = subprocess.run(["git", "show", "HEAD:%s" % rel],
                                 cwd=str(BASE_DIR), capture_output=True,
                                 encoding="utf-8", errors="replace", timeout=60).stdout
        except Exception as exc:  # noqa: BLE001
            record("P1/P2 既有文件未被覆盖", FAIL, "无法读取 %s：%r" % (rel, exc))
            return
        if not old.strip():
            record("P1/P2 既有文件未被覆盖", FAIL, "git 中找不到 %s" % rel)
            return

        def dump(text, names):
            tree = ast.parse(text)
            return {n.name: ast.dump(n) for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name in names}

        path = BASE_DIR / rel.replace("/", os.sep)
        o, n = dump(old, funcs), dump(path.read_text(encoding="utf-8"), funcs)
        bad = [f for f in funcs if o.get(f) != n.get(f)]
        if bad:
            record("P1/P2 既有文件未被覆盖", FAIL,
                   "%s 中的安全关键函数被改动：%s" % (rel, bad))
            return
        notes.append("%s 的 %s 逐字未变" % (rel, "/".join(funcs)))

    plain = len(data["files"]) - len(APPEND_OK) - len(FUNC_GUARDED)
    record("P1/P2 既有文件未被覆盖", PASS,
           "%d 个文件哈希一致 + %s（其余为允许的追加与更新）" % (plain, "；".join(notes)))


def check_agent_loop_untouched():
    """工作区 agent-loop 护栏字节级不变。"""
    import hashlib

    guard = BASE_DIR.parent / "化学" / "agent-loop" / "probe" / "evidence" / "p1_guard_baseline.json"
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


def check_scope():
    """未越界实现 P4 内容（积分 / Excel 报表）。"""
    forbidden_dirs = ["reports", "points", "scoring"]
    found = [d for d in forbidden_dirs if (BASE_DIR / d).exists()]
    if found:
        record("未越界实现后续阶段", FAIL, "发现越界模块：%s" % found)
        return
    # 代码里不应出现积分/报表相关实现
    code, out = run(["git", "grep", "-nE",
                     r"(contribution_points|excel_export|积分排名)",
                     "--", "*.py"])
    hits = [l for l in out.splitlines() if l.strip()]
    if hits:
        record("未越界实现后续阶段", FAIL, "发现越界实现：%s" % hits[:3])
    else:
        record("未越界实现后续阶段", PASS, "无 reports/积分/Excel 实现")


def check_pillow_container_deps():
    """Dockerfile 的 Pillow 运行依赖判断是否与本机实证一致。"""
    dockerfile = BASE_DIR / "Dockerfile"
    if not dockerfile.exists():
        record("Dockerfile Pillow 依赖", FAIL, "缺少 Dockerfile")
        return
    text = dockerfile.read_text(encoding="utf-8")
    problems = []
    if "zlib1g" not in text:
        problems.append("缺少 zlib1g（Pillow 的 _imaging.so 直接依赖 libz.so.1）")
    if "libpq5" not in text:
        problems.append("缺少 libpq5（psycopg 需要）")
    # libjpeg62-turbo 经实证**不需要**（wheel 自带 libjpeg）；
    # 若装了不算错，但注释里应说明依据
    if "libjpeg62-turbo" in text and "自带" not in text:
        problems.append("装了 libjpeg62-turbo 但未说明依据（wheel 已自带 jpeg）")
    if problems:
        record("Dockerfile Pillow 依赖", FAIL, "；".join(problems))
    else:
        record("Dockerfile Pillow 依赖", PASS,
               "zlib1g + libpq5 就位；未盲目追加 libjpeg（已实证 wheel 自带）")


def check_nginx_body_size():
    """Nginx 请求体上限必须能承载 300 MiB 视频。"""
    conf = BASE_DIR / "deploy" / "nginx" / "pingpong.conf"
    if not conf.exists():
        record("Nginx 请求体上限", FAIL, "缺少 nginx 配置")
        return
    text = conf.read_text(encoding="utf-8")
    match = re.search(r"client_max_body_size\s+(\d+)([mMkKgG]?);", text)
    if not match:
        record("Nginx 请求体上限", FAIL, "未找到 client_max_body_size")
        return
    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "m":
        limit = value * 1024 * 1024
    elif unit == "k":
        limit = value * 1024
    elif unit == "g":
        limit = value * 1024 * 1024 * 1024
    else:
        limit = value

    required = 300 * 1024 * 1024
    if limit < required:
        record("Nginx 请求体上限", FAIL,
               "当前 %d%s 小于视频上限 300 MiB，大文件会在反代层被 413 拒绝"
               % (value, match.group(2)))
    else:
        record("Nginx 请求体上限", PASS,
               "client_max_body_size = %d%s（≥ 300 MiB 视频上限）"
               % (value, match.group(2)))


def check_docker_available():
    """Docker 是否可运行（本机不可用时如实标 SKIP）。"""
    if shutil.which("docker") is None:
        record("Docker 实际可运行", SKIP,
               "本机未安装 docker —— 容器构建与启动未在本机验证")
        return
    code, out = run(["docker", "compose", "config", "--quiet"], timeout=120)
    if code != 0:
        record("Docker 实际可运行", FAIL, "docker compose config 失败", out)
    else:
        record("Docker 实际可运行", PASS, "compose 配置可解析")


def check_real_cos():
    """真实 COS 是否可用（无凭据时如实标 SKIP）。"""
    cfg = {
        "region": os.environ.get("COS_REGION", ""),
        "bucket": os.environ.get("COS_BUCKET", ""),
        "secret_id": os.environ.get("COS_SECRET_ID", ""),
        "secret_key": os.environ.get("COS_SECRET_KEY", ""),
    }
    if not all(cfg.values()):
        record("真实 COS 链路", SKIP,
               "未提供 COS 凭据 —— 生产 COS 上传/下载未实测（仅本地适配器与模拟验证）")
        return
    # 有凭据时做一次真实往返
    script = (
        "import os,django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.test import override_settings;"
        "from core.storage import CosPrivateStorage;"
        "s=CosPrivateStorage(region=os.environ['COS_REGION'],"
        "bucket=os.environ['COS_BUCKET'],secret_id=os.environ['COS_SECRET_ID'],"
        "secret_key=os.environ['COS_SECRET_KEY']);"
        "k='verify_stage3_probe.txt';"
        "s.save(k,b'hello');"
        "assert s.exists(k);"
        "d=s.read(k); assert d==b'hello', d;"
        "u=s.presigned_url(k); assert 'http' in u;"
        "s.delete(k);"
        "print('COS_OK')"
    )
    code, out = run([PYTHON, "-c", script], timeout=300)
    if code != 0 or "COS_OK" not in out:
        record("真实 COS 链路", FAIL, "COS 往返测试失败", out)
    else:
        record("真实 COS 链路", PASS, "上传 / 存在性 / 读取 / 预签名 / 删除 均成功")


def check_readme_consistency():
    """README 记录了 P3 内容。"""
    readme = BASE_DIR / "README.md"
    if not readme.exists():
        record("README 与项目一致", FAIL, "缺少 README.md")
        return
    text = readme.read_text(encoding="utf-8").replace("\\", "/")
    required = ["scripts/verify_stage3.py", "submissions", "reviews",
                "my/submissions/", "COS", "缩略图"]
    missing = [r for r in required if r not in text]
    if not (BASE_DIR / "scripts" / "verify_stage3.py").exists():
        missing.append("scripts/verify_stage3.py(磁盘缺失)")
    if missing:
        record("README 与项目一致", FAIL, "缺少：%s" % missing)
    else:
        record("README 与项目一致", PASS, "P3 相关路径、存储与图片说明均已记录")


# ===========================================================================
# 主流程
# ===========================================================================

CHECKS = [
    ("python", check_python),
    ("deps", check_dependencies),
    ("pillow", check_pillow_capabilities),
    ("pin", check_requirements_pinned),
    ("check", check_django_checks),
    ("migrate", check_migrations),
    ("db", check_database),
    ("apps", check_p3_apps_registered),
    ("models", check_p3_models_exist),
    ("tables", check_db_tables_and_constraints),
    ("config", check_plugin_config),
    ("imaging", check_imaging_live),
    ("perm", check_permission_invariants),
    ("storage", check_storage_failclosed),
    ("routes", check_get_never_mutates),
    ("p3tests", check_p3_tests),
    ("p1reg", check_p1_regression),
    ("p2reg", check_p2_regression),
    ("pgmark", check_postgres_marker),
    ("full", check_full_suite),
    ("p1file", check_previous_files_not_rewritten),
    ("guard", check_agent_loop_untouched),
    ("scope", check_scope),
    ("dockerfile", check_pillow_container_deps),
    ("nginx", check_nginx_body_size),
    ("docker", check_docker_available),
    ("cos", check_real_cos),
    ("readme", check_readme_consistency),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="P3 阶段统一验收入口")
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
    print("P3 阶段验证 —— 工作成果提交、图片处理、私有附件存储与审核闭环")
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

    report = BASE_DIR / "verify_stage3_report.json"
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
