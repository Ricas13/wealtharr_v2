"""Owner-only advisory alert matrix; no strategy evaluation and no brokerage actions.

Category is deliberately fixed at code level. Preferences select a subset of
enabled destinations. The underlying NotificationDelivery unique key,
three-attempt retry budget and encrypted transport stay authoritative.
"""
from .models import NotificationDelivery, NotificationPreference
from .services import dispatch

CATEGORIES = (
    ("quarterly", "Quarterly instruction"),
    ("urgent", "Big market drop"),
    ("data", "Price problem"),
    ("followup", "Review reminder"),
    ("contribution", "Monthly deposit reminder"),
)
CATEGORY_KEYS = {value for value, _ in CATEGORIES}
# A glyph at the start of each alert's subject so it stands out in a chat or inbox.
CATEGORY_EMOJI = {"quarterly": "📈", "urgent": "🚨", "data": "⚠️", "followup": "⏰", "contribution": "💰"}
CHANNELS = ("email", "discord", "telegram")
# Where alerts are delivered. A strategy routes an alert to "discord"; it then
# goes to each Discord destination the person switched on: a DM from the
# site's bot and/or a webhook into a channel on their own server.
DELIVERY_CHANNELS = ("email", "discord", "discord_webhook", "telegram")
ROUTE_FOR = {"email": "email", "discord": "discord", "discord_webhook": "discord", "telegram": "telegram"}


def selected_channels(pref, category):
    """Destinations switched on in Settings; which alerts go where is per strategy."""
    if category not in CATEGORY_KEYS:
        raise ValueError("Unknown notification category")
    return tuple(channel for channel in DELIVERY_CHANNELS
                 if getattr(pref, channel + "_enabled", False))


def strategy_channels(pref, category, strategy=None):
    """Intersect switched-on destinations with the strategy's explicit routes.

    Legacy strategies without a route matrix preserve existing owner-wide
    preferences; a v2 enrolment with missing/malformed routes fails closed.
    """
    channels = selected_channels(pref, category)
    if strategy is None:
        return channels
    routes = strategy.alert_routes
    if not isinstance(routes, dict) or not routes:
        return () if strategy.strategy_key else channels
    kind = "unacted" if category == "followup" else category
    selected = routes.get(kind)
    if not isinstance(selected, dict):
        return ()
    return tuple(c for c in channels if selected.get(ROUTE_FOR[c]) is True)


def route_notice(owner, category, event_key, subject, message, *,
                 sender=None, strategy=None):
    """Return per-channel status, distinguishing old SENT from new delivery.

    No false 'sent' results for muted/unconfigured owner channels. Transport
    readiness is handled by dispatch, whose retry status is persisted.
    """
    if strategy is not None and strategy.owner_id != owner.pk:
        raise ValueError("Strategy does not belong to notification owner")
    pref, _ = NotificationPreference.objects.get_or_create(owner=owner)
    channels = strategy_channels(pref, category, strategy)
    if not channels:
        return {}
    previous = set(NotificationDelivery.objects.filter(
        owner=owner, event_key=event_key, channel__in=channels, status="SENT"
    ).values_list("channel", flat=True))
    # The production transport persists the category for future opt-out-safe
    # retries. Explicit injected senders remain compatible with existing tests.
    emoji = CATEGORY_EMOJI.get(category)
    if emoji and not subject.startswith(emoji):
        subject = emoji + " " + subject
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
        ("new-money:", "contribution"),
        ("contrib:", "contribution"),
    ):
        if key.startswith(prefix):
            return category
    return None


def newly_delivered(outcomes):
    return sum(status == "sent" for status in outcomes.values())
