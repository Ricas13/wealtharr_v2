"""Owner-only advisory alert matrix; no strategy evaluation and no brokerage actions.

Category is deliberately fixed at code level. Preferences select a subset of
enabled destinations. The underlying NotificationDelivery unique key,
three-attempt retry budget and encrypted transport stay authoritative.
"""
from .models import NotificationDelivery, NotificationPreference
from .services import dispatch

CATEGORIES = (
    ("quarterly", "Quarterly decision digest"),
    ("urgent", "Urgent strategy warnings"),
    ("data", "Missing or unverified market information"),
    ("followup", "Outstanding review follow-up"),
    ("contribution", "Monthly planned contribution"),
)
CATEGORY_KEYS = {value for value, _ in CATEGORIES}
CHANNELS = ("email", "discord")


def selected_channels(pref, category):
    if category not in CATEGORY_KEYS:
        raise ValueError("Unknown notification category")
    return tuple(channel for channel in CHANNELS
                 if getattr(pref, channel + "_enabled", False)
                 and getattr(pref, category + "_" + channel, False)
                 and (category != "contribution" or pref.monthly_reminders))


def route_notice(owner, category, event_key, subject, message):
    """Return per-channel status, distinguishing old SENT from new delivery.

    No false 'sent' results for muted/unconfigured owner channels. Transport
    readiness is handled by dispatch, whose retry status is persisted.
    """
    pref, _ = NotificationPreference.objects.get_or_create(owner=owner)
    channels = selected_channels(pref, category)
    if not channels:
        return {}
    previous = set(NotificationDelivery.objects.filter(
        owner=owner, event_key=event_key, channel__in=channels, status="SENT"
    ).values_list("channel", flat=True))
    outcomes = dispatch(owner, event_key, subject, message, channels=channels)
    return {channel: ("already_sent" if channel in previous and status == "sent"
                      else status) for channel, status in outcomes.items()}


def newly_delivered(outcomes):
    return sum(status == "sent" for status in outcomes.values())
