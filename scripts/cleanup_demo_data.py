#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清理 P3 验证/演示期间产生的数据。

只删除**测试与演示**留下的内容，绝不碰真实业务数据：
    · privacy: 只处理带约定前缀的标记数据
    · 前缀：[演示数据] / [P3验证] / [浏览器验证] / [P2浏览器验证]
    · 用户：D-* / P3-* 这些演示账号
    · VF3-*：验收脚本的临时数据（正常情况下验收脚本会自己清理）

删除顺序遵循 PROTECT 依赖链（引用方 → 被引用方），
否则会触发 ProtectedError —— 这是 P3 有意设计的"历史不因删账号而消失"。

用法：
    python scripts/cleanup_demo_data.py --dry-run   # 先看会删什么
    python scripts/cleanup_demo_data.py             # 真正删除
"""
import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

NAME_MARKS = ["[演示数据]", "[P3验证]", "[P2浏览器验证]", "[浏览器验证]", "[VF3]"]
USER_PREFIXES = ["D-", "P3-", "VF3-"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不删除")
    args = ap.parse_args()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    if "prod" in os.environ.get("DJANGO_SETTINGS_MODULE", ""):
        print("拒绝执行：当前指向生产配置。本脚本只允许在开发库上运行。")
        return 2

    import django

    django.setup()

    from django.contrib.auth import get_user_model
    from django.db.models import Q

    from competitions.models import Competition, ReviewerAssignment
    from submissions.models import (
        Attachment,
        OrphanObject,
        Submission,
        SubmissionVersion,
        UserImageUsage,
    )
    from tasks.models import (
        CompetitionTask,
        TaskAssignment,
        TaskTemplate,
        WorkType,
    )
    from reviews.models import ReviewLog

    User = get_user_model()

    def marked_users():
        q = Q()
        for p in USER_PREFIXES:
            q |= Q(student_id__startswith=p)
        return User.objects.filter(q)

    def marked_competitions():
        q = Q()
        for m in NAME_MARKS:
            q |= Q(name__startswith=m)
        return Competition.objects.filter(q)

    # --- 统计 ---
    users = marked_users()
    comps = marked_competitions()
    subs = Submission.objects.filter(assignment__member__in=users)
    subs_alt = Submission.objects.filter(assignment__task__competition__in=comps)
    all_subs = (subs | subs_alt).distinct()
    atts = Attachment.objects.filter(version__submission__in=all_subs)

    print("=" * 66)
    print("将清理以下内容（仅限标记为演示/测试的数据）")
    print("=" * 66)
    print("  演示账号        : %d" % users.count())
    print("  演示比赛        : %d" % comps.count())
    print("  工作记录        : %d" % all_subs.count())
    print("  提交版本        : %d" % SubmissionVersion.objects.filter(
        submission__in=all_subs).count())
    print("  附件            : %d" % atts.count())
    print("  审核记录        : %d" % ReviewLog.objects.filter(
        submission__in=all_subs).count())
    print("  配额记录        : %d" % UserImageUsage.objects.filter(user__in=users).count())
    print("  比赛/任务/模板  : %d / %d / %d" % (
        comps.count(),
        CompetitionTask.objects.filter(competition__in=comps).count(),
        TaskTemplate.objects.filter(
            Q(competition_tasks__competition__in=comps)).distinct().count(),
    ))

    if args.dry_run:
        print("\n（dry-run，未删除任何数据）")
        return 0

    # --- 删除附件文件（先于数据库记录）---
    from core.storage import get_storage

    store = get_storage()
    keys = [a.storage_key for a in atts if a.storage_key]
    removed_files = 0
    for key in keys:
        try:
            if store.delete(key):
                removed_files += 1
        except Exception:  # noqa: BLE001
            pass

    # --- 按 PROTECT 依赖链删除数据库记录 ---
    ReviewLog.objects.filter(submission__in=all_subs).delete()
    Attachment.objects.filter(version__submission__in=all_subs).delete()
    SubmissionVersion.objects.filter(submission__in=all_subs).delete()
    Submission.objects.filter(pk__in=list(all_subs.values_list("pk", flat=True))).delete()
    ReviewerAssignment.objects.filter(
        Q(competition__in=comps) | Q(reviewer__in=users)).delete()
    TaskAssignment.objects.filter(
        Q(member__in=users) | Q(task__competition__in=comps)).delete()
    CompetitionTask.objects.filter(competition__in=comps).delete()
    TaskTemplate.objects.filter(name__startswith="[").filter(
        competition_tasks__isnull=True).delete()
    WorkType.objects.filter(name__startswith="[").filter(
        templates__isnull=True).delete()
    Competition.objects.filter(pk__in=list(comps.values_list("pk", flat=True))).delete()
    UserImageUsage.objects.filter(user__in=users).delete()
    User.objects.filter(pk__in=list(users.values_list("pk", flat=True))).delete()

    print()
    print("已清理：")
    print("  删除附件文件    : %d 个" % removed_files)
    print("  剩余演示账号    : %d" % marked_users().count())
    print("  剩余演示比赛    : %d" % marked_competitions().count())
    print("  剩余工作记录    : %d" % all_subs.count())
    print("  未处理的待清理对象: %d（这些是存储删除失败留下的，需另行处理）"
          % OrphanObject.objects.filter(resolved=False).count())
    return 0


if __name__ == "__main__":
    sys.exit(main())
