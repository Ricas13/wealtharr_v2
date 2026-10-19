"""Single owner alert scheduler entrypoint; safe to run hourly."""
from django.core.management.base import BaseCommand
from tracker.notification_scheduler import run_notification_scheduler

class Command(BaseCommand):
    help = "Run owner alert matrix, quarterly digest, reminders and due retries once"
    def handle(self, *args, **kwargs):
        result = run_notification_scheduler()
        self.stdout.write(
            "Owner notifications: %s newly delivered; %s retries attempted; "
            "%s retry deliveries; run %s %s" % (
                result.new_deliveries, result.attempted_retries,
                result.successful_retries, result.pk, result.status))
