# -*- coding: utf-8 -*-
"""清理失败后残留的孤立存储对象。

为什么需要这个命令
------------------
COS 不在 PostgreSQL 事务内，因此「上传成功但写库失败」时要回删对象。
若回删也失败（网络抖动、权限问题），系统会把对象键记进
`submissions.OrphanObject` —— **不假装已经清理**（P3 明确要求）。
本命令就是那个「可追溯状态」的兑付方式：由运维/管理员择机重试删除。

用法：
    python manage.py cleanup_orphans --dry-run     # 只列出，不删除
    python manage.py cleanup_orphans               # 真正重试删除
    python manage.py cleanup_orphans --older-than 7   # 只处理 7 天前的

设计取舍：
    · 默认只处理**已存在且仍未被引用**的对象 —— 万一某个 key 其实已经被
      新记录引用（理论上不该发生），删掉会破坏正常数据，所以先查引用；
    · 删除成功才标记 resolved；失败保持待清理，下次继续重试；
    · 全程只打印摘要，不泄漏存储凭据。
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.storage import get_storage
from submissions.models import Attachment, OrphanObject


class Command(BaseCommand):
    help = "重试删除因写库失败而残留的孤立存储对象"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="只列出将处理的对象，不实际删除",
        )
        parser.add_argument(
            "--older-than", type=int, default=0, metavar="DAYS",
            help="只处理创建时间早于 N 天的记录（默认 0 = 全部）",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        older_than = options["older_than"]

        qs = OrphanObject.objects.filter(resolved=False).order_by("created_at")
        if older_than > 0:
            cutoff = timezone.now() - timezone.timedelta(days=older_than)
            qs = qs.filter(created_at__lt=cutoff)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("没有待清理的孤立对象。"))
            return

        self.stdout.write("待清理对象：%d 个" % total)

        store = get_storage()
        resolved = 0
        still_ref = 0
        failed = 0

        for orphan in qs:
            # 安全检查：该对象是否其实已被某条附件引用？
            # 若被引用，说明它并不是孤立的，**不能删**。
            if Attachment.objects.filter(storage_key=orphan.storage_key).exists():
                still_ref += 1
                self.stdout.write(
                    "  [跳过] %s —— 已被附件引用，不可删除" % orphan.storage_key)
                continue

            if dry_run:
                self.stdout.write("  [将删除] %s（创建于 %s）"
                                  % (orphan.storage_key,
                                     orphan.created_at.strftime("%Y-%m-%d %H:%M")))
                continue

            try:
                # delete() 返回 False 表示对象本就不存在（例如从未上传成功）——
                # 这种情况同样可以标记为已解决
                store.delete(orphan.storage_key)
                orphan.mark_resolved()
                resolved += 1
                self.stdout.write("  [已删除] %s" % orphan.storage_key)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.stderr.write(
                    "  [失败] %s —— %s（保留待清理，下次重试）"
                    % (orphan.storage_key, type(exc).__name__))

        self.stdout.write("")
        if dry_run:
            self.stdout.write(self.style.WARNING(
                "dry-run：未删除任何对象（%d 个待处理）" % total))
            return

        self.stdout.write(self.style.SUCCESS(
            "完成：已清理 %d，跳过（仍被引用）%d，失败 %d，剩余待清理 %d"
            % (resolved, still_ref, failed,
               OrphanObject.objects.filter(resolved=False).count())))
