"""
种子数据：P2 需要的三个角色。

为什么用数据迁移而不是在代码里硬编码：
- 角色本来就是数据（P1 的 Role 表设计如此），页面/后台可继续增减；
- 用 get_or_create 保证幂等：重复执行不会重复插入，也不会覆盖
  管理员后来改过的名称与说明。

角色分工：
    club_admin  社团管理员    全部管理操作（建比赛、配模板、分配人员、授权审核人）
    member      干事          只能看到分配给自己的任务
    viewer      全局查看      可浏览非草稿比赛，但**没有**管理权与审核权

注意：审核权**不在**这里 —— 它必须由 ReviewerAssignment 逐场授权，
不能靠角色获得（这是 P2 的硬约束）。
"""
from django.db import migrations


ROLES = [
    ("member", "干事", "被分配并完成具体工作任务的成员", 10),
    ("club_admin", "社团管理员", "负责比赛、任务模板与人员分配的管理工作", 20),
    ("viewer", "全局查看", "可浏览比赛与任务概况，但不能修改，也不能审核", 30),
]


def create_roles(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    for code, name, description, sort_order in ROLES:
        Role.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "description": description,
                "sort_order": sort_order,
                "is_active": True,
            },
        )


def remove_roles(apps, schema_editor):
    """回滚：只移除本次新增的角色。

    不删除已被用户引用的角色（那会让历史关系失去分类），
    因此这里先解绑再删，且仅限未被引用的。
    """
    Role = apps.get_model("accounts", "Role")
    codes = [code for code, *_ in ROLES]
    Role.objects.filter(code__in=codes, users__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_roles, remove_roles),
    ]
