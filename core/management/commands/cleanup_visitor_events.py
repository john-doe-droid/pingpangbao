"""Delete analytics records outside the retention window."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import VisitorEvent


class Command(BaseCommand):
    help = "Delete visitor analytics older than the retention period."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise ValueError("--days 必须大于 0")
        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = VisitorEvent.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS("已清理 %d 条访问记录。" % deleted))
