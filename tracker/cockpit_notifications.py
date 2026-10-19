"""Owner notification scheduler: one digest, typed warnings and planned reminders.

This module never calculates signals, mutates holdings or executes trades.
The delivery layer retains persistent per-channel idempotency and retries.
"""
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

from django.utils import timezone
from .models import (
    LedgerEntry, NotificationPreference, RebalanceExecution,
    SignalFill, StrategyInstance, StrategySignal,
)
from .services import dispatch, ledger_summary
from .notification_routing import route_notice, newly_delivered
from .cockpit import _price_status
from .market_signals import quarter_end_before

D = Decimal


def trade_lines(signal):
    lines = []
    for action in signal.actions:
        if action.get("side") not in ("BUY", "SELL"):
            continue
        lines.append("%s approximately £%s of %s (%s units at %s close)" % (
            action["side"].title(), action["estimated_gbp"],
            action["symbol"], action["quantity"], signal.as_of))
    return lines


def _send(owner, category, key, subject, message):
    return newly_delivered(route_notice(
        owner, category, key, subject, message, sender=dispatch))


def send_due_cockpit_notices(today=None, remind_after_days=3):
    """Send owner-selected, deterministic advisory notices only once per key.

    Previously recorded partial fills suppress stale trade quantities, but
    a review may still get one weekly reminder to complete reconciliation.
    """
    today = today or timezone.localdate()
    count = 0
    for strategy in StrategyInstance.objects.select_related("owner", "account").filter(
            preset__slug="nasdaq-quarterly-target",
            market_data_enabled=True).exclude(account__kind="PAPER"):
        owner = strategy.owner
        pref, _ = NotificationPreference.objects.get_or_create(owner=owner)
        report = ledger_summary(strategy)
        fresh, why, marks = _price_status(strategy, report)
        # A recurring source issue is never converted to a trade instruction.
        # One digest per calendar week and problem identity until resolved.
        if not fresh and report["positions"]:
            fingerprint = sha256(why.encode("utf-8")).hexdigest()[:12]
            week = today.isocalendar()
            key = "data:%s:%04d-W%02d:%s" % (
                strategy.pk, week.year, week.week, fingerprint)
            count += _send(owner, "data", key,
                "Wealtharr — review market data",
                "%s: an accurate review is currently unavailable. %s "
                "Open Today to see the data status. Do not trade from old "
                "recommendations." % (strategy.name, why))

        recent = StrategySignal.objects.filter(
            strategy=strategy, kind="QUARTERLY",
            as_of=quarter_end_before(today)).exclude(
                review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE"),
            ).order_by("-as_of", "-pk").first()
        if recent and not RebalanceExecution.objects.filter(signal=recent).exists():
            actions = trade_lines(recent)
            existing = SignalFill.objects.filter(strategy=strategy, signal=recent)
            partial = existing.exists()
            # A proposal must not be sent with invented or stale quantities
            # after any unrelated broker event. Stored partials are factual.
            reconciled = (fresh and not strategy.ledger.filter(
                occurred_on__gt=recent.as_of).exists())
            if actions and reconciled and not partial:
                count += _send(owner, "quarterly",
                    "quarterly-digest:%s:%s" % (strategy.pk, recent.signal_key),
                    "Wealtharr — quarterly review (%s)" % recent.as_of,
                    "Recorded review %s\n\n%s\n\n"
                    "Open Wealtharr → Today. Check broker availability, actual "
                    "prices, quantities and fees. No orders have been placed." % (
                        recent.as_of, "\n".join(actions)))
            if (actions and recent.created_at.date() <= today - timedelta(
                    days=remind_after_days) and (partial or reconciled)):
                week = today.isocalendar()
                label = ("You have saved actual partial fills. Complete the "
                         "review only after all broker fills and verified "
                         "post-fill GBP closing values are available."
                         if partial else
                         "This review is not confirmed. Open Today, verify "
                         "freshness and record only actual broker fills.")
                count += _send(owner, "followup",
                    "rebalance-reminder:%s:%s:%04d-W%02d" % (
                        strategy.pk, recent.signal_key, week.year, week.week),
                    "Wealtharr — outstanding quarterly review",
                    "%s: %s No automatic trades are placed." % (
                        strategy.name, label))

        if (pref.monthly_reminders and strategy.contribution_alerts and
                strategy.monthly_target > 0 and today.day >= 3):
            contributed = sum((row.amount for row in strategy.ledger.filter(
                kind="DEPOSIT", occurred_on__year=today.year,
                occurred_on__month=today.month)), D("0"))
            if contributed < strategy.monthly_target:
                count += _send(owner, "contribution",
                    "monthly:%s:%04d-%02d" % (
                        strategy.pk, today.year, today.month),
                    "Wealtharr — planned monthly £%s contribution" % (
                        strategy.monthly_target),
                    "Your plan this month: £%s. Actual recorded: £%s. "
                    "Amount still planned: £%s. After depositing in "
                    "Trading 212, record it in Wealtharr. This is a "
                    "reminder, not an automatic transfer." % (
                        strategy.monthly_target, contributed,
                        strategy.monthly_target - contributed))
    return count
