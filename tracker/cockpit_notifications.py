"""Owner notification scheduler: one digest, typed warnings and planned reminders.

This module never calculates signals, mutates holdings or executes trades.
The delivery layer retains persistent per-channel idempotency and retries.
"""
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

from django.utils import timezone
from .models import (
    LedgerEntry, NotificationDelivery, NotificationPreference, RebalanceExecution,
    SignalFill, StrategyInstance, StrategySignal,
)
from .services import dispatch, ledger_summary
from .notification_routing import route_notice, newly_delivered
from .signal_status import _price_status, changed_since_review
from .strategies.base import DAILY_SUPERSEDED_NOTE
from .strategies.registry import current_review_range, plugin_keys

D = Decimal



def review_tag(signal):
    """The review's key in alert keys. A recalculated review reuses its quarter's
    key, so it also gets its own id: its new numbers then go out instead of the
    first, retired review's stored message (or nothing, if that one was sent)."""
    replaced = signal.strategy.signals.filter(
        signal_key__startswith=signal.signal_key + ":superseded:").exists()
    return "%s:%s" % (signal.signal_key, signal.pk) if replaced else signal.signal_key

def symbol_for(currency):
    from .money import symbol_for as lookup
    return lookup(currency)


def trade_lines(signal):
    lines = []
    for action in signal.actions:
        if action.get("side") not in ("BUY", "SELL"):
            continue
        lines.append("%s approximately %s%s of %s (%s units at %s close)" % (
            action["side"].title(), symbol_for(signal.strategy.account.currency), action["estimated_gbp"],
            action["symbol"], action["quantity"], signal.as_of))
    return lines


def _send(strategy, owner, category, key, subject, message):
    return newly_delivered(route_notice(
        owner, category, key, subject, message,
        sender=dispatch, strategy=strategy))


def send_due_cockpit_notices(today=None, remind_after_days=None):
    """Send owner-selected, deterministic advisory notices only once per key.

    Previously recorded partial fills suppress stale trade quantities, but
    a review may still get one weekly reminder to complete reconciliation.
    """
    today = today or timezone.localdate()
    count = 0
    for strategy in StrategyInstance.objects.select_related("owner", "account").filter(
            preset__slug__in=plugin_keys()).exclude(account__kind="PAPER"):
        owner = strategy.owner
        pref, _ = NotificationPreference.objects.get_or_create(owner=owner)
        delay = (remind_after_days if remind_after_days is not None
                 else pref.followup_delay_days)
        delay = max(1, min(30, delay))
        report = ledger_summary(strategy)
        fresh, why, marks = _price_status(strategy, report)
        # A recurring source issue is never converted to a trade instruction.
        # One digest per calendar week and problem identity until resolved.
        if (strategy.market_data_enabled and not fresh and report["positions"]
                and not why.startswith("Your newest transaction is later than")):
            fingerprint = sha256(why.encode("utf-8")).hexdigest()[:12]
            week = today.isocalendar()
            key = "data:%s:%04d-W%02d:%s" % (
                strategy.pk, week.year, week.week, fingerprint)
            count += _send(strategy, owner, "data", key,
                "Wealtharr — review market data",
                "%s: an accurate review is currently unavailable. %s "
                "Open Today to see the data status. Do not trade from old "
                "recommendations." % (strategy.name, why))

        recent = StrategySignal.objects.filter(
            strategy=strategy, kind="QUARTERLY",
            as_of__range=current_review_range(strategy, today)).exclude(
                review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE"),
            ).order_by("-as_of", "-pk").first()
        if (strategy.market_data_enabled and recent and
                not RebalanceExecution.objects.filter(signal=recent).exists()):
            actions = trade_lines(recent)
            existing = SignalFill.objects.filter(strategy=strategy, signal=recent)
            partial = existing.exists()
            # Same rule as Today: new money after quarter-end keeps the
            # proposal valid; trades, withdrawals or corrections supersede it.
            superseded = changed_since_review(strategy, recent)
            reconciled = fresh and not superseded
            if superseded and not partial:
                # Never retry a digest whose advice no longer holds.
                NotificationDelivery.objects.filter(
                    owner=owner, event_key="quarterly-digest:%s:%s" % (strategy.pk, review_tag(recent)),
                    status__in=("PENDING", "FAILED", "SENDING"),
                ).update(status="CANCELLED", next_retry_at=None, error="Review superseded")
            if actions and reconciled and not partial:
                count += _send(strategy, owner, "quarterly",
                    "quarterly-digest:%s:%s" % (strategy.pk, review_tag(recent)),
                    "Wealtharr — quarterly review (%s)" % recent.as_of,
                    "Recorded review %s\n\n%s\n\n"
                    "Open Wealtharr → Today. Check broker availability, actual "
                    "prices, quantities and fees. No orders have been placed." % (
                        recent.as_of, "\n".join(actions)))
            if (actions and recent.created_at.date() <= today - timedelta(
                    days=delay) and (partial or reconciled)):
                week = today.isocalendar()
                label = ("You have saved actual partial fills. Complete the "
                         "review only after all broker fills and verified "
                         "post-fill GBP closing values are available."
                         if partial else
                         "This review is not confirmed. Open Today, verify "
                         "freshness and record only actual broker fills.")
                count += _send(strategy, owner, "followup",
                    "rebalance-reminder:%s:%s:%04d-W%02d" % (
                        strategy.pk, review_tag(recent), week.year, week.week),
                    "Wealtharr — outstanding quarterly review",
                    "%s: %s No automatic trades are placed." % (
                        strategy.name, label))

        # An instruction the owner was told about, replaced before any fill:
        # say so, so nobody acts on the earlier alert.
        withdrawn = StrategySignal.objects.filter(
            strategy=strategy, kind="QUARTERLY", review_outcome="NOT_APPLICABLE",
            review_note=DAILY_SUPERSEDED_NOTE, as_of__gte=today - timedelta(days=14))
        for old in withdrawn:
            if not NotificationDelivery.objects.filter(
                    owner=owner, event_key="quarterly-digest:%s:%s" % (
                        strategy.pk, review_tag(old))).exists():
                continue  # Never alerted, nothing to retract.
            if SignalFill.objects.filter(strategy=strategy, signal=old).exists():
                continue
            shape = [(a.get("side"), a.get("symbol")) for a in old.actions]
            if any([(a.get("side"), a.get("symbol")) for a in later.actions] == shape
                   for later in StrategySignal.objects.filter(
                       strategy=strategy, kind="QUARTERLY", as_of__gt=old.as_of
                   ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE"))):
                continue  # Same orders re-issued with fresh numbers: its own alert covers it.
            count += _send(strategy, owner, "quarterly",
                "withdrawn:%s:%s" % (strategy.pk, old.pk),
                "Wealtharr — earlier instruction withdrawn (%s)" % old.as_of,
                "%s: the review of %s is no longer current because the rules now "
                "say something different. Do not act on that alert. Open Today "
                "for the current instruction, if any. No orders were placed." % (
                    strategy.name, old.as_of))

        # A confirmed new deposit should not sit unnoticed until the next
        # quarter or year. This is an opt-in contribution-category *nudge*;
        # never email estimated orders (those may become stale before delivery).
        if (strategy.market_data_enabled and not strategy.tracking_only
                and fresh and not (recent and trade_lines(recent)
                    and not RebalanceExecution.objects.filter(signal=recent).exists())):
            from .contribution_guidance import new_money_guidance
            suggestion = new_money_guidance(strategy, report, prices_ready=True, today=today)
            if suggestion:
                key = "new-money:%s:%s:%s" % (
                    strategy.pk, suggestion["deposit_id"], suggestion["last_trade_id"])
                count += _send(strategy, owner, "contribution", key,
                    "Wealtharr — new cash ready for investment",
                    "%s: a confirmed deposit is waiting to be invested. "
                    "Open Today for the CURRENT strategy-specific suggestion. "
                    "Do not place trades from this message; cash and prices can "
                    "change. No orders have been placed." % strategy.name)

        if (pref.monthly_reminders and strategy.contribution_alerts and
                strategy.monthly_target > 0 and today.day >= 3):
            from .v2_ledger_core import effective_entries
            contributed = sum((row.amount for row in effective_entries(strategy)
                               if row.kind == "DEPOSIT" and row.occurred_on.year == today.year
                               and row.occurred_on.month == today.month), D("0"))
            if contributed < strategy.monthly_target:
                count += _send(strategy, owner, "contribution",
                    "monthly:%s:%04d-%02d" % (
                        strategy.pk, today.year, today.month),
                    "Wealtharr — planned monthly %s%s contribution" % (
                        symbol_for(strategy.account.currency), strategy.monthly_target),
                    "Your plan this month: {c}{plan}. Actual recorded: {c}{done}. "
                    "Amount still planned: {c}{left}. After depositing with "
                    "{broker}, record it in Wealtharr. This is a "
                    "reminder, not an automatic transfer.".format(
                        c=symbol_for(strategy.account.currency), plan=strategy.monthly_target,
                        done=contributed, left=strategy.monthly_target - contributed,
                        broker=strategy.account.broker_label))
    return count
