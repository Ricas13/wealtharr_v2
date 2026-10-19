"""Section H scheduled advisory delivery with bounded retries and safe diagnostics.

No transports, bank/broker access or investment calculations are run here.
Use the send_cockpit_notifications hourly command; the legacy due
command delegates to the same method to avoid second reminder streams.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import NotificationDelivery, NotificationSchedulerRun
from .services import dispatch
from .cockpit_notifications import send_due_cockpit_notices

MAX_RETRY_ROWS = 100


def retry_due_deliveries(*, as_of=None):
    now = as_of or timezone.now()
    rows = NotificationDelivery.objects.select_related("owner").filter(
        attempts__lt=3
    ).filter(
        Q(status__in=("FAILED", "PENDING")) |
        Q(status="SENDING", last_attempt_at__lt=now - timedelta(minutes=15))
    ).filter(
        Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)
    ).order_by("created_at", "pk")[:MAX_RETRY_ROWS]
    attempted = 0
    sent = 0
    for row in rows:
        if not row.subject or not row.message:
            continue
        outcome = dispatch(
            row.owner, row.event_key, row.subject, row.message,
            channels=(row.channel,))
        if outcome.get(row.channel) in ("sent", "failed", "retrying"):
            attempted += 1
        if outcome.get(row.channel) == "sent":
            sent += 1
    return attempted, sent


def run_notification_scheduler(*, today=None):
    """Persist coarse scheduler health without leaking channel credentials."""
    run = NotificationSchedulerRun.objects.create(name="owner_notices")
    try:
        delivered = send_due_cockpit_notices(today=today)
        attempted, retried = retry_due_deliveries()
    except Exception:
        run.status = "FAILED"
        run.error_code = "SCHEDULED_DELIVERY_ERROR"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_code", "finished_at"])
        raise
    run.new_deliveries = delivered
    run.attempted_retries = attempted
    run.successful_retries = retried
    run.status = "SUCCEEDED"
    run.finished_at = timezone.now()
    run.save(update_fields=[
        "new_deliveries", "attempted_retries", "successful_retries",
        "status", "finished_at"])
    return run
