"""Retire quarterly reviews that a later ledger change made stale.

A review is computed from the ledger as it stood. When the owner adds, edits,
deletes or imports an entry dated on or before an unexecuted review, that
review is marked not applicable (kept for history) and the next price run
recalculates the quarter from the corrected ledger.
"""
from django.utils import timezone

from .models import RebalanceExecution, SignalFill


def retire_stale_reviews(strategy, changed_on):
    if changed_on is None:
        return 0
    retired = 0
    stale = strategy.signals.filter(kind="QUARTERLY", as_of__gte=changed_on).exclude(
        review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE"))
    for signal in stale:
        # Never rewrite a review the owner has already acted on.
        if (RebalanceExecution.objects.filter(signal=signal).exists() or
                SignalFill.objects.filter(signal=signal).exists()):
            continue
        signal.review_outcome = "NOT_APPLICABLE"
        signal.review_note = (
            f"Superseded on {timezone.localdate():%d %b %Y}: the ledger changed "
            f"(entry dated {changed_on:%d %b %Y}). Recalculated on the next price run."
        )
        signal.signal_key = f"{signal.signal_key}:superseded:{signal.pk}"[:80]
        signal.save(update_fields=["review_outcome", "review_note", "signal_key"])
        retired += 1
    return retired


def rebuild_value_history(strategy, changed_on):
    """Recompute saved daily values on or after a corrected date.

    Each value is rebuilt from the corrected ledger and the verified GBP close
    stored for that same day; a day that can no longer be valued is dropped
    rather than left showing a pre-correction figure. Returns rows touched.
    """
    from .models import MarketObservation
    from .services import money, replay
    from .v2_ledger_core import effective_entries
    if changed_on is None:
        return 0
    entries = sorted(effective_entries(strategy), key=lambda e: e.occurred_on)
    touched = 0
    for row in strategy.value_snapshots.filter(as_of__gte=changed_on):
        cash, net, positions = replay(e for e in entries if e.occurred_on <= row.as_of)
        value = cash
        for symbol, qty in positions.items():
            close = MarketObservation.objects.filter(
                symbol=symbol, as_of=row.as_of, currency="GBP").first() if qty > 0 else None
            if close is None:
                value = None
                break
            value += qty * close.price
        if value is None or cash < 0:
            row.delete()
        else:
            row.total_value, row.net_contributed = money(value), money(net)
            row.save(update_fields=["total_value", "net_contributed"])
        touched += 1
    return touched
