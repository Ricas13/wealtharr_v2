"""9Sig's own dashboard cards: growth vs the quarter's signal line, quarter by quarter."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from tracker.cockpit_execution import proposed_actions
from tracker.core.calendar import quarter_end_before
from tracker.models import MarketObservation, RebalanceExecution, SignalFill
from tracker.v2_breakdown import _spy_reference, _trace
from tracker.v2_ledger_core import effective_entries, replay

ZERO = Decimal("0")
PENNY = Decimal("0.01")
HUNDRED = Decimal("100")


def growth_target(strategy, report, next_end):
    """This quarter's growth target against the growth value now."""
    if strategy.tracking_only:
        return None
    try:
        trace, reason = _trace(strategy, next_end)
    except (ValidationError, ArithmeticError):
        trace, reason = None, "The ledger can't be replayed."
    if trace is None:
        return {"available": False, "reason": reason}
    mark = report["marks"].get(strategy.equity_symbol)
    quantity = report["positions"].get(strategy.equity_symbol, ZERO)
    growth = quantity * mark.price if mark else None
    difference = growth - trace["line"] if growth is not None else None
    try:
        _, spy = _spy_reference(quarter_end_before(timezone.localdate()))
    except (ValidationError, ArithmeticError):
        spy = None
    fill = None
    if growth is not None and trace["line"] > ZERO:
        top = max(growth, trace["line"]) * Decimal("1.15")
        fill = {"growth": float(growth / top * 100), "target": float(trace["line"] / top * 100)}
    return {
        "available": True, "target": trace["line"], "growth": growth,
        "difference": difference,
        "difference_pct": (difference / trace["line"] * HUNDRED).quantize(Decimal("0.1"))
        if difference is not None and trace["line"] > ZERO else None,
        "spy": spy, "bar": fill, "quarter_end": next_end,
    }


def quarter_rows(strategy, next_end, report):
    """Quarter by quarter: line target vs actual growth value, with the saved review."""
    try:
        trace, _ = _trace(strategy, next_end)
    except (ValidationError, ArithmeticError):
        trace = None
    if trace is None:
        return ()
    entries = list(effective_entries(strategy))
    today = timezone.localdate()
    signals = {s.as_of: s for s in strategy.signals.filter(kind="QUARTERLY").exclude(
        review_outcome="NOT_APPLICABLE").order_by("as_of", "pk")}
    rows = []
    for item in trace["quarters"]:
        end = item["end"]
        current = end >= today
        if current:
            mark = report["marks"].get(strategy.equity_symbol)
            qty = report["positions"].get(strategy.equity_symbol, ZERO)
            growth = qty * mark.price if mark else None
        else:
            try:
                held = replay([e for e in entries if e.occurred_on <= end])["positions"]
            except (ValidationError, ArithmeticError):
                held = {}
            close = MarketObservation.objects.filter(
                symbol=strategy.equity_symbol, currency="GBP", as_of__lte=end,
                as_of__gte=end - timedelta(days=6)).order_by("-as_of").first()
            qty = held.get(strategy.equity_symbol, ZERO)
            growth = qty * close.price if close and qty else None
        signal = signals.get(end)
        trades = proposed_actions(signal) if signal else ()
        status = "progress" if current else "none"
        if signal and trades:
            if RebalanceExecution.objects.filter(signal=signal).exists():
                done = RebalanceExecution.objects.get(signal=signal).executed_on
                status = "done"
            elif SignalFill.objects.filter(signal=signal).exists():
                status, done = "partial", None
            else:
                status, done = "todo", None
        else:
            done = None
        growth_side = next((a for a in trades if a.get("symbol") == strategy.equity_symbol), None)
        rows.append({
            "label": item["quarter"].replace("-", " "), "end": end,
            "opening": item["opening"], "target": item["closing"].quantize(PENNY),
            "growth": growth.quantize(PENNY) if growth is not None else None,
            "difference": (growth - item["closing"]).quantize(PENNY) if growth is not None else None,
            "signal": signal, "current": current, "status": status, "done": done,
            "side": growth_side.get("side") if growth_side else "",
            "amount": Decimal(str(growth_side.get("estimated_gbp"))) if growth_side and growth_side.get("estimated_gbp") else None,
        })
    return tuple(reversed(rows))
