#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
演示 / 联调数据种子脚本。

用途：在**本地开发库**里造一套可走通全流程的数据，便于用浏览器实际验证
「建比赛 → 配模板 → 建任务 → 分配人员 → 干事看我的任务」这条链路。

⚠️ 只能在开发库上跑。脚本会先检查 DJANGO_SETTINGS_MODULE，
   生产配置（config.settings.prod）下直接拒绝执行 —— 避免误操作真实数据。

幂等：用固定学号/名称 get_or_create，重复执行不会造出重复数据。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\seed_demo_data.py
    .\\.venv\\Scripts\\python.exe scripts\\seed_demo_data.py --reset   # 先清掉本脚本建的数据
"""
import argparse
import os
import sys
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

DEMO_IDS = ["D-ADMIN", "D-MEMBER1", "D-MEMBER2", "D-VIEWER"]
DEMO_PASSWORD = "DemoPassw0rd!2026"
DEMO_MARK = "[演示数据]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="先删除本脚本建的数据")
    args = ap.parse_args()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

    if "prod" in os.environ.get("DJANGO_SETTINGS_MODULE", ""):
        print("拒绝执行：当前指向生产配置。本脚本只允许在开发库上运行。")
        return 2

    import django

    django.setup()

    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from accounts.models import Role
    from competitions.models import Competition, CompetitionStatus, ReviewerAssignment
    from tasks.models import (
        CompetitionTask,
        TaskAssignment,
        TaskTemplate,
        WorkType,
    )

    User = get_user_model()

    def ensure_role(code, name, order):
        role, _ = Role.objects.get_or_create(
            code=code, defaults={"name": name, "sort_order": order})
        return role

    role_admin = ensure_role("club_admin", "社团管理员", 20)
    role_member = ensure_role("member", "干事", 10)
    role_viewer = ensure_role("viewer", "全局查看", 30)

    def ensure_user(sid, name, password=DEMO_PASSWORD, roles=(), **extra):
        user = User.objects.filter(student_id=sid).first()
        if user is None:
            user = User.objects.create_user(
                student_id=sid, password=password, real_name=name, **extra)
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
        for role in roles:
            user.roles.add(role)
        return user

    # --- 清理 ---
    if args.reset:
        Competition.objects.filter(name__startswith=DEMO_MARK).delete()
        TaskTemplate.objects.filter(name__startswith=DEMO_MARK).delete()
        WorkType.objects.filter(name__startswith=DEMO_MARK).delete()
        User.objects.filter(student_id__in=DEMO_IDS).delete()
        print("已清理上一轮演示数据。")

    # --- 账号 ---
    admin = ensure_user("D-ADMIN", "演示管理员", roles=[role_admin])
    m1 = ensure_user("D-MEMBER1", "演示干事甲", roles=[role_member])
    m2 = ensure_user("D-MEMBER2", "演示干事乙", roles=[role_member])
    viewer = ensure_user("D-VIEWER", "演示查看员", roles=[role_viewer])

    # --- 工作类型 ---
    wt_posters = WorkType.objects.get_or_create(
        name="%s 宣传海报" % DEMO_MARK,
        defaults={"created_by": admin, "sort_order": 10})[0]
    wt_article = WorkType.objects.get_or_create(
        name="%s 推文撰写" % DEMO_MARK,
        defaults={"created_by": admin, "sort_order": 20})[0]

    # --- 模板 ---
    tpl_poster, _ = TaskTemplate.objects.get_or_create(
        name="%s 赛后海报" % DEMO_MARK,
        defaults={
            "work_type": wt_posters, "created_by": admin,
            "description": "为比赛制作一张宣传海报，需附设计图。",
            "allow_text": True, "require_text": True,
            "allow_image": True, "require_image": True,
            "max_records_per_member": 1,
        },
    )
    tpl_article, _ = TaskTemplate.objects.get_or_create(
        name="%s 赛后推文" % DEMO_MARK,
        defaults={
            "work_type": wt_article, "created_by": admin,
            "description": "撰写一篇赛后回顾推文，需附链接。",
            "allow_text": True, "require_text": True,
            "allow_image": False, "require_image": False,
            "allow_link": True, "require_link": True,
            "max_records_per_member": 2,
        },
    )

    # --- 比赛（进行中）---
    competition, _ = Competition.objects.get_or_create(
        name="%s 2026 秋季新生杯" % DEMO_MARK,
        defaults={
            "event_date": timezone.localdate(),
            "default_deadline": timezone.now() + timedelta(days=7),
            "description": "面向新生的校内交流赛。",
            "status": CompetitionStatus.ACTIVE,
            "created_by": admin,
        },
    )
    if competition.status != CompetitionStatus.ACTIVE:
        competition.status = CompetitionStatus.ACTIVE
        competition.save(update_fields=["status", "updated_at"])

    # --- 任务（从模板，带规则快照）---
    def ensure_task(template, name, deadline=None):
        task = CompetitionTask.objects.filter(
            competition=competition, template=template).first()
        if task is None:
            task = CompetitionTask.build_from_template(
                competition=competition, template=template, name=name,
                deadline=deadline, created_by=admin)
            task.save()
        return task

    task_poster = ensure_task(tpl_poster, "%s 制作赛前海报" % DEMO_MARK)
    task_article = ensure_task(
        tpl_article, "%s 撰写赛后推文" % DEMO_MARK,
        deadline=timezone.now() + timedelta(days=3),   # 演示"任务独立截止"
    )

    # --- 分配 ---
    for task, members in ((task_poster, [m1, m2]), (task_article, [m1])):
        for member in members:
            TaskAssignment.objects.get_or_create(
                task=task, member=member,
                defaults={"assigned_by": admin, "is_active": True})

    # --- 审核人授权（只给干事甲，演示"审核权需逐场授权"）---
    ReviewerAssignment.objects.get_or_create(
        competition=competition, reviewer=m1,
        defaults={"granted_by": admin, "is_active": True})

    print("=" * 66)
    print("演示数据已就绪")
    print("=" * 66)
    print("比赛：%s（进行中）" % competition.name)
    print("任务：")
    for task in competition.tasks.all():
        n = task.assignments.filter(is_active=True).count()
        print("   - %s → 已分配 %d 人；截止 %s（%s）"
              % (task.name, n, task.effective_deadline.strftime("%Y-%m-%d %H:%M"),
                 task.deadline_source))
    print()
    print("账号（密码统一为 %s，首次登录会要求改密）" % DEMO_PASSWORD)
    print("  演示管理员  D-ADMIN     （社团管理员）")
    print("  演示干事甲  D-MEMBER1   （干事；被分配 2 个任务，且是该比赛的审核人）")
    print("  演示干事乙  D-MEMBER2   （干事；只被分配 1 个任务）")
    print("  演示查看员  D-VIEWER    （全局查看；无管理权、无审核权）")
    print()
    print("说明：这些账号 must_change_password=False，可直接登录演示。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
