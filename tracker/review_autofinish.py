"""Finish a fully recorded quarterly review once the next close is in.

The owner's "I did this" fills are the evidence; this only runs the same
checked finalization the Finish button runs (every fill recorded, a verified
GBP close after the last fill, nothing else changed since). Reset reviews
set a new baseline, so they still wait for the owner to finish them.
"""
from django.core.exceptions import ValidationError
from django.utils import timezone

from .cockpit_execution import completion_rows, finalize_manual_review
from .market_signals import quarter_end_before
from .models import RebalanceExecution, SignalFill, StrategySignal


def auto_finish_reviews(today=None):
    """Finish every ready review; returns how many were finished. Idempotent."""
    quarter = quarter_end_before(today or timezone.localdate())
    finished = 0
    signals = StrategySignal.objects.select_related("strategy").filter(
        kind="QUARTERLY", as_of=quarter, reset_reason="",
        strategy__tracking_only=False,
    ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE", "CONFIRMED"))
    for signal in signals:
        if RebalanceExecution.objects.filter(signal=signal).exists():
            continue
        fills = list(SignalFill.objects.filter(signal=signal, strategy=signal.strategy)
                     .select_related("ledger_entry"))
        if not fills:
            continue
        try:
            rows = completion_rows(signal, fills)
            if not rows or not all(row["complete"] for row in rows):
                continue
            finalize_manual_review(owner=signal.strategy.owner,
                                   strategy_id=signal.strategy_id, signal_id=signal.pk)
        except ValidationError:
            continue  # Not ready yet (usually waiting for the next close).
        finished += 1
    return finished
