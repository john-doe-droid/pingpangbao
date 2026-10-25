"""Create the initial club administrator and member accounts."""

import csv
import os
import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Role, User


class Command(BaseCommand):
    help = "Create admin01..adminNN and member01..memberNN with one-time passwords."

    def add_arguments(self, parser):
        parser.add_argument("--admins", type=int, default=5)
        parser.add_argument("--members", type=int, default=27)
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        admin_count = options["admins"]
        member_count = options["members"]
        if admin_count < 0 or member_count < 0:
            raise CommandError("账号数量不能为负数。")

        try:
            admin_role = Role.objects.get(code="club_admin", is_active=True)
            member_role = Role.objects.get(code="member", is_active=True)
        except Role.DoesNotExist as exc:
            raise CommandError("基础角色未初始化，请先运行数据库迁移。") from exc

        created_rows = []
        with transaction.atomic():
            for prefix, count, label, role in (
                ("admin", admin_count, "管理员", admin_role),
                ("member", member_count, "干事", member_role),
            ):
                for number in range(1, count + 1):
                    account = "%s%02d" % (prefix, number)
                    if User.objects.filter(account=account).exists():
                        continue
                    password = secrets.token_urlsafe(12)
                    user = User.objects.create_user(
                        account=account,
                        password=password,
                        real_name="%s%02d" % (label, number),
                        must_change_password=True,
                    )
                    user.roles.add(role)
                    created_rows.append(
                        (account, user.real_name, label, password, "是")
                    )

        with open(options["output"], "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["账号", "姓名", "角色", "初始密码", "首次登录须改密"])
            writer.writerows(created_rows)
        os.chmod(options["output"], 0o600)

        self.stdout.write(
            self.style.SUCCESS(
                "已创建 %d 个账号；已存在账号未重置。密码表：%s"
                % (len(created_rows), options["output"])
            )
        )
