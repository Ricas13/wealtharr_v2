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


def instance_category_enabled(strategy, category, channel):
    """A new instance's fixed alert matrix is an extra restrictive gate.

    Legacy records without a pinned v2 strategy key retain owner-wide routing.
    "unacted" is the UI label for the existing followup event category.
    """
    if strategy is None or not getattr(strategy, "strategy_key", ""):
        return True
    keys = {"followup": "unacted"}
    name = keys.get(category, category)
    return bool((strategy.alert_routes or {}).get(name, {}).get(channel, False))


def route_notice(owner, category, event_key, subject, message, *, sender=None,
                 strategy=None):
    """Return per-channel status, distinguishing old SENT from new delivery.

    No false 'sent' results for muted/unconfigured owner channels. Transport
    readiness is handled by dispatch, whose retry status is persisted.
    """
    if strategy is not None and strategy.owner_id != owner.pk:
        return {}
    pref, _ = NotificationPreference.objects.get_or_create(owner=owner)
    channels = tuple(ch for ch in selected_channels(pref, category)
                     if instance_category_enabled(strategy, category, ch))
    if not channels:
        return {}
    previous = set(NotificationDelivery.objects.filter(
        owner=owner, event_key=event_key, channel__in=channels, status="SENT"
    ).values_list("channel", flat=True))
    # The production transport persists the category for future opt-out-safe
    # retries. Explicit injected senders remain compatible with existing tests.
    transport = sender or dispatch
    if transport is dispatch:
        outcomes = transport(owner, event_key, subject, message,
                             channels=channels, category=category, strategy=strategy)
    else:
        outcomes = transport(owner, event_key, subject, message,
                             channels=channels)
    return {channel: ("already_sent" if channel in previous and status == "sent"
                      else status) for channel, status in outcomes.items()}


def category_for_event_key(key):
    """Typed routing for persisted legacy and Section H events during retries."""
    for prefix, category in (
        ("quarterly-digest:", "quarterly"),
        ("signal:", "urgent"),
        ("data:", "data"),
        ("rebalance-reminder:", "followup"),
        ("monthly:", "contribution"),
        ("contrib:", "contribution"),
    ):
        if key.startswith(prefix):
            return category
    return None


def newly_delivered(outcomes):
    return sum(status == "sent" for status in outcomes.values())
