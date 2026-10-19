"""Manually retry persisted notifications without recalculating any signal."""
from django.core.management.base import BaseCommand
from tracker.notification_scheduler import retry_due_deliveries

class Command(BaseCommand):
    help = "Retry at most 100 due failed/stale deliveries with max 3 attempts"
    def handle(self, *args, **kwargs):
        attempted, sent = retry_due_deliveries()
        self.stdout.write("Notification retry: %s attempted; %s delivered" %
                          (attempted, sent))
