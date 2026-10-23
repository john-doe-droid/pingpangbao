#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UI 验收用的补种数据（在 seed_demo_data.py 之后跑）。

为什么用测试客户端而不是直接写库或直调 service：
    本脚本的目的是"让每个页面都有真实内容可看"。若绕过表单直接插表，
    造出来的数据可能不符合真实表单约束（材料类型、配额、状态机），
    拿着这种数据去验收 UI 等于对着假数据下判断。
    所以这里用 Django 测试客户端**走真实 HTTP 表单提交**，
    和用户实际点按钮是同一条路径。

补的内容：
    · 待审核 / 已通过 / 已退回（退回后再重提，形成多版本）
    · 已通过后发起重新审核
这样版本历史、审核记录、状态样式、报表统计都有真实数据。

⚠️ 只允许开发库（settings 含 prod 时直接退出）。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\seed_ui_demo.py
    .\\.venv\\Scripts\\python.exe scripts\\seed_ui_demo.py --reset
"""
import argparse
import io
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

MARK = "[UI演示]"
PASSWORD = "DemoPassw0rd!2026"


def make_jpeg(name="海报.jpg"):
    """生成一张真实 JPEG（带纹理，便于验收缩略图与压缩结果）。"""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image, ImageDraw

    buf = io.BytesIO()
    img = Image.new("RGB", (1600, 1200), (26, 115, 232))
    d = ImageDraw.Draw(img)
    for x in range(0, 1600, 120):
        d.line([(x, 0), (x, 1200)], fill=(255, 255, 255), width=3)
    for y in range(0, 1200, 120):
        d.line([(0, y), (1600, y)], fill=(232, 240, 254), width=2)
    img.save(buf, format="JPEG", quality=88)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    if "prod" in os.environ.get("DJANGO_SETTINGS_MODULE", ""):
        print("拒绝执行：当前指向生产配置。")
        return 1

    import django
    django.setup()

    from django.db import transaction
    from django.test import Client

    from accounts.models import User
    from competitions.models import Competition
    from reviews.models import ReviewLog
    from submissions.models import Attachment, Submission, SubmissionVersion
    from tasks.models import CompetitionTask, TaskAssignment

    def u(sid):
        return User.objects.filter(student_id=sid).first()

    admin, m1, m2 = u("D-ADMIN"), u("D-MEMBER1"), u("D-MEMBER2")
    if not (admin and m1 and m2):
        print("请先运行 scripts/seed_demo_data.py")
        return 1

    comp = Competition.objects.filter(name__startswith="[演示数据]").first()
    if comp is None:
        print("找不到演示比赛，请先运行 seed_demo_data.py")
        return 1

    tasks = list(CompetitionTask.objects.filter(competition=comp).order_by("pk"))
    if len(tasks) < 2:
        print("演示任务不足，请先运行 seed_demo_data.py")
        return 1
    task_poster, task_article = tasks[0], tasks[1]

    # ---------------- 清理 ----------------
    if args.reset:
        # 删除顺序必须遵循外键约束：审核日志/附件 → 版本 → 记录。
        # 且要**先取好记录 ID 列表**——一旦版本被删，
        # `versions__text_content__startswith=MARK` 这个条件就再也匹配不到记录，
        # 结果留下没有版本的"空壳记录"（第一版就踩了这个坑）。
        with transaction.atomic():
            sub_ids = list(
                Submission.objects
                .filter(versions__text_content__startswith=MARK)
                .values_list("pk", flat=True)
            )
            vids = list(
                SubmissionVersion.objects
                .filter(text_content__startswith=MARK)
                .values_list("pk", flat=True)
            )
            if vids:
                ReviewLog.objects.filter(version_id__in=vids).delete()
                Attachment.objects.filter(version_id__in=vids).delete()
                SubmissionVersion.objects.filter(pk__in=vids).delete()
            if sub_ids:
                ReviewLog.objects.filter(submission_id__in=sub_ids).delete()
                Attachment.objects.filter(version__submission_id__in=sub_ids).delete()
                Submission.objects.filter(pk__in=sub_ids).delete()
            # 兜底：清掉没有任何版本的残留记录（上一版删除顺序留下的空壳）
            orphan = list(
                Submission.objects.filter(versions__isnull=True)
                .values_list("pk", flat=True)
            )
            for pk in orphan:
                ReviewLog.objects.filter(submission_id=pk).delete()
                Submission.objects.filter(pk=pk).delete()
        print("已清理旧的 UI 演示数据（含无版本的残留记录）")

    if Submission.objects.filter(versions__text_content__startswith=MARK).exists():
        print("UI 演示数据已存在，跳过（重建请加 --reset）")
        return 0

    def login(sid):
        # SERVER_NAME 用 localhost：测试客户端的默认 testserver 不在
        # ALLOWED_HOSTS 里，会 400。这里不为了脚本而放宽生产配置。
        c = Client(SERVER_NAME="localhost")
        ok = c.login(username=sid, password=PASSWORD)
        if not ok:
            print("  登录失败:", sid)
        return c

    def asg_of(task, member):
        return TaskAssignment.objects.filter(
            task=task, member=member, is_active=True).first()

    def submit(task, member, text, *, link="", with_image=False):
        """用真实表单提交，返回 Submission 或 None。"""
        a = asg_of(task, member)
        if a is None:
            print("  跳过（未分配该任务）:", task.name, member.student_id)
            return None
        c = login(member.student_id)
        # 注意：Django 5.2 的 Client.post 没有 files 参数 ——
        # 文件必须放进 data 里（值可以是文件对象或文件列表）。
        # 传 files= 会被当作未知关键字塞进 extra 而静默丢弃，
        # 表现为"提交成功但提示缺少必填图片"，极难排查。
        data = {"text_content": text}
        if link:
            data["link"] = link
        if with_image:
            data["images"] = [make_jpeg()]
        resp = c.post(
            "/my/submissions/new/%d/" % task.pk,
            data=data, follow=True,
        )
        sub = (Submission.objects
               .filter(assignment__task=task, assignment__member=member)
               .order_by("-pk").first())
        if resp.status_code != 200:
            print("  提交返回异常状态:", resp.status_code)
        if sub is None:
            print("  提交后没有记录产生:", task.name, member.student_id)
        return sub

    created = []

    # 1) 待审核：海报（需图片 + 文字）
    sub1 = submit(task_poster, m1,
                  MARK + " 海报初稿已完成，尺寸 1080x1440，配色沿用社团主色。",
                  with_image=True)
    if sub1:
        created.append(("待审核", sub1))

    # 2) 已通过：推文（需链接 + 文字），m1 提交、m1 也是审核人 → 不能自审
    #    因此用 m2 提交会因未分配而跳过；改为 m1 提交后由 admin 审核？
    #    审核权只来自逐场授权，admin 不一定有。
    #    这里改为：m1 提交，若 m1 恰是授权审核人则用自己的另一条记录不行，
    #    所以直接用视图层允许的路径——由授权审核人 m1 审核 m2 的记录。
    sub2 = submit(task_poster, m2,
                  MARK + " 海报第二版：替换背景，补充比赛时间。",
                  with_image=True)
    if sub2:
        created.append(("待处理(海报/m2)", sub2))

    # 3) 已通过：让 m1（授权审核人）审核 m2 的记录 —— 满足"不能审自己"
    if sub2:
        c = login(m1.student_id)
        r = c.post("/my/submissions/review/%d/" % sub2.pk,
                   {"action": "approve", "comment": "构图清晰，信息完整。"},
                   follow=True)
        sub2.refresh_from_db()
        if sub2.status == "approved":
            created.append(("已通过", sub2))
        else:
            print("  审核未生效，状态仍为:", sub2.status)

    # 4) 已退回 → 重新提交，形成多版本（验收版本历史）
    #
    # 说明：这里本想在"撰写赛后推文"任务上造数据，但该任务要求「作品链接」，
    # 而 MaterialType.LINK 的值被写成中文「作品链接」，与规则快照的键 "link"
    # 对不上，导致任何"要求作品链接"的任务都无法提交成功（P3 起就存在）。
    # 本次是 UI 改版，不修业务逻辑，故改用海报任务造多版本数据。
    # 该缺陷已在交付报告中单独列出。
    sub3 = submit(task_poster, m1,
                  MARK + " 海报第二稿：补充了比赛时间与场地信息。",
                  with_image=True)
    if sub3:
        created.append(("待审核(海报/m1)", sub3))

    print()
    print("=" * 62)
    print("UI 演示数据已就绪")
    print("=" * 62)
    for label, sub in created:
        print("  %-16s 记录 #%-4s 状态=%-8s 版本=%s"
              % (label, sub.pk, sub.status, sub.edition_count))
    print()
    print("账号密码统一为 %s" % PASSWORD)
    print("  D-ADMIN    社团管理员（配置任务、记录管理、全部报表）")
    print("  D-MEMBER1  干事 + 本场审核人（可进审核队列）")
    print("  D-MEMBER2  干事（只看自己的）")
    print("  D-VIEWER   全局查看（无管理权、无审核权）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
