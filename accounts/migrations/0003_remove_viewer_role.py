"""移除没有独立权限能力的 viewer 角色。

普通登录用户已经可以查看非草稿比赛，viewer 与普通账号没有行为差异。
删除角色时，Django 会自动清理用户与角色之间的关联；用户账号本身保留。
"""
from django.db import migrations


def remove_viewer_role(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.filter(code="viewer").delete()


def restore_viewer_role(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.get_or_create(
        code="viewer",
        defaults={
            "name": "全局查看",
            "description": "旧版兼容角色",
            "sort_order": 30,
            "is_active": True,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_seed_p2_roles"),
    ]

    operations = [
        migrations.RunPython(remove_viewer_role, restore_viewer_role),
    ]
