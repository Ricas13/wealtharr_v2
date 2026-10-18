"""Retry transient notifications without regenerating or duplicating financial signals."""
from django.core.management.base import BaseCommand
from django.utils import timezone
from tracker.models import NotificationDelivery
from tracker.services import dispatch


class Command(BaseCommand):
    help = "Retry pending/failed email or Discord deliveries, max three attempts each"

    def handle(self, *args, **kwargs):
        now = timezone.now()
        due = NotificationDelivery.objects.select_related("owner").filter(
            status__in=("FAILED", "PENDING"), attempts__lt=3,
        ).order_by("created_at")[:100]
        attempted = 0
        sent = 0
        for row in due:
            if not row.subject or not row.message:
                # Legacy, pre-payload failures need an administrator to resubmit.
                continue
            if row.next_retry_at and row.next_retry_at > now:
                continue
            result = dispatch(row.owner, row.event_key, row.subject, row.message,
                              channels=(row.channel,))
            attempted += 1
            if result.get(row.channel) == "sent":
                sent += 1
        self.stdout.write("Notification retry: %s attempted; %s delivered" % (attempted, sent))
