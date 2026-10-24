"""把原生视频材料迁移为作品链接，并移除模板视频开关。"""
from django.db import migrations


def video_to_link(apps, schema_editor):
    TaskTemplate = apps.get_model("tasks", "TaskTemplate")
    CompetitionTask = apps.get_model("tasks", "CompetitionTask")

    for template in TaskTemplate.objects.all().iterator():
        require_link = template.require_link or template.require_video
        allow_link = template.allow_link or template.allow_video or require_link
        template.allow_link = allow_link
        template.require_link = require_link
        template.allow_video = False
        template.require_video = False
        template.save(update_fields=[
            "allow_link", "require_link", "allow_video", "require_video",
        ])

    for task in CompetitionTask.objects.all().iterator():
        snapshot = dict(task.rule_snapshot or {})
        require_link = bool(
            snapshot.get("require_link") or snapshot.get("require_video")
        )
        allow_link = bool(
            snapshot.get("allow_link")
            or snapshot.get("allow_video")
            or require_link
        )
        snapshot["allow_link"] = allow_link
        snapshot["require_link"] = require_link
        snapshot.pop("allow_video", None)
        snapshot.pop("require_video", None)
        task.rule_snapshot = snapshot
        task.save(update_fields=["rule_snapshot"])


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(video_to_link, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="tasktemplate",
            name="template_require_video_implies_allow",
        ),
        migrations.RemoveField(
            model_name="tasktemplate",
            name="allow_video",
        ),
        migrations.RemoveField(
            model_name="tasktemplate",
            name="require_video",
        ),
    ]
