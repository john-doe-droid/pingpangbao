from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_remove_viewer_role"),
    ]

    operations = [
        migrations.RenameField(
            model_name="user",
            old_name="student_id",
            new_name="account",
        ),
        migrations.AlterField(
            model_name="user",
            name="account",
            field=models.CharField(
                help_text="用于登录的账号，可使用已有编号或社团自定义名称",
                max_length=32,
                unique=True,
                verbose_name="账号",
            ),
        ),
        migrations.CreateModel(
            name="LoginThrottle",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name="ID",
                )),
                ("key", models.CharField(
                    max_length=64, unique=True, verbose_name="限流键",
                )),
                ("failure_count", models.PositiveSmallIntegerField(
                    default=0, verbose_name="连续失败次数",
                )),
                ("window_started_at", models.DateTimeField(
                    verbose_name="统计窗口开始时间",
                )),
                ("locked_until", models.DateTimeField(
                    blank=True, null=True, verbose_name="锁定截止时间",
                )),
                ("updated_at", models.DateTimeField(
                    auto_now=True, verbose_name="更新时间",
                )),
            ],
            options={
                "verbose_name": "登录限流",
                "verbose_name_plural": "登录限流",
                "indexes": [
                    models.Index(
                        fields=["locked_until"],
                        name="accounts_lo_locked__d5c0d4_idx",
                    ),
                ],
            },
        ),
    ]
