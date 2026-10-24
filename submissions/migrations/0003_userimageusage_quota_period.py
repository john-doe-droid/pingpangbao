from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0002_orphanobject"),
    ]

    operations = [
        migrations.AddField(
            model_name="userimageusage",
            name="quota_period_start",
            field=models.DateField(
                blank=True,
                help_text="用于区分春季/秋季学期；为空时会在下次提交时重新核算",
                null=True,
                verbose_name="当前配额周期开始日期",
            ),
        ),
    ]
