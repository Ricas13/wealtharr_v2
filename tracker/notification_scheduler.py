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
from .notification_routing import category_for_event_key, selected_channels
from .models import NotificationPreference
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
        category = row.category or category_for_event_key(row.event_key)
        pref, _ = NotificationPreference.objects.get_or_create(owner=row.owner)
        if category:
            try:
                permitted = row.channel in selected_channels(pref, category)
            except ValueError:
                permitted = False
        else:
            permitted = bool(getattr(pref, row.channel + "_enabled", False))
        if not permitted:
            # Audit remains, but opt-out permanently retires the old dated
            # notice rather than delivering it after preferences change again.
            NotificationDelivery.objects.filter(pk=row.pk, status=row.status).update(
                status="CANCELLED", next_retry_at=None, error="Owner opt-out")
            continue
        if not row.subject or not row.message:
            continue
        outcome = dispatch(
            row.owner, row.event_key, row.subject, row.message,
            channels=(row.channel,), category=category or "")
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
    # Optional owner-operated dead-man's switch; never include its URL or token
    # in logs, database diagnostic fields or /health/ output.
    _ping_dead_mans_switch()
    return run


def _ping_dead_mans_switch():
    """Best-effort external success ping; network failure must not lose a run."""
    import os
    from urllib.parse import urlsplit
    from urllib.request import Request, urlopen

    url = os.environ.get("WEALTHARR_SCHEDULER_PING_URL", "").strip()
    if not url:
        return False
    parsed = urlsplit(url)
    # Do not call arbitrary insecure URLs or loopback/private hosts by default.
    if (parsed.scheme != "https" or not parsed.hostname or
            parsed.username or parsed.password or parsed.fragment):
        return False
    import ipaddress
    try:
        addr = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        addr = None
    if (addr is not None and not addr.is_global) or parsed.hostname in (
            "localhost", "localhost.localdomain"):
        return False
    try:
        with urlopen(Request(url, method="GET",
                             headers={"User-Agent": "Wealtharr/2.0"}),
                     timeout=5) as response:
            return 200 <= response.status < 300
    except Exception:
        return False
