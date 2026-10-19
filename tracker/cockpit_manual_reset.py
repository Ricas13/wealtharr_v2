"""Explicit owner-elected 60/40 reset alternative after a v2 empty-reserve alert.

Never changes the rule engine's all-in recommendation. Instead creates a
separately attributable, immutable reset REVIEW from verified quarter-end
positions and quotes. Uses the existing actual-fill and verified post-fill
baseline confirmation path; does not place trades.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .cockpit_execution import _check_current
from .market_signals import NINE_SIG_RULE_VERSION, quarter_end_before, settlement_close
from .models import (
    RebalanceExecution, SignalFill, StrategyBaseline, StrategyInstance,
    StrategySignal, YahooSync,
)
from .services import ledger_summary
from .strategies.nine_sig.v2 import _floor_qty, _order

D = Decimal
ZERO = D("0")
CENT = D("0.01")
SLEEVE = D("0.60")
RESERVE_EMPTY_NOTICE = "Your reserve is now empty. Review whether to reset to 60/40."


@transaction.atomic
def choose_manual_reserve_reset(*, owner, strategy_id, signal_id):
    """One explicit POST, idempotent: preserve original advice and owner choice.

    The rule-generated shortfall still proposes the all-in purchase. The owner
    alone may request an alternative 60/40 plan, which is not a confirmed reset
    unless the existing later-dated single-fill workflow succeeds.
    """
    strategy = StrategyInstance.objects.select_for_update().select_related(
        "preset", "account").get(pk=strategy_id, owner=owner)
    original = StrategySignal.objects.select_for_update().get(
        pk=signal_id, strategy=strategy)
    existing = StrategySignal.objects.filter(
        strategy=strategy, signal_key="owner-reset:" + original.signal_key
    ).first()
    if existing:
        return existing
    _check_current(strategy, original)
    if (original.strategy_version != NINE_SIG_RULE_VERSION or
            original.reset_reason or original.kind != "QUARTERLY" or
            RESERVE_EMPTY_NOTICE not in original.message):
        raise ValidationError("Only the current v2 exhausted-reserve review can offer a manual reset.")
    if (not strategy.market_data_enabled or
            original.as_of != quarter_end_before(timezone.localdate()) or
            SignalFill.objects.filter(strategy=strategy, signal=original).exists() or
            RebalanceExecution.objects.filter(strategy=strategy, signal=original).exists() or
            StrategyBaseline.objects.filter(strategy=strategy, as_of__gt=original.as_of).exists() or
            strategy.ledger.filter(occurred_on__gt=original.as_of).exists()):
        raise ValidationError(
            "A newer transaction, fill, reset, or disabled market feed requires a fresh manual review.")
    if YahooSync.objects.filter(
        symbol__in=(strategy.equity_symbol, strategy.safe_symbol, "SPY", "TQQQ"),
        last_error__startswith="PRICE_HOLD:",
    ).exists():
        raise ValidationError("A market reference is on hold; no new reset plan is available.")

    equity = settlement_close(strategy.equity_symbol, original.as_of)
    safe = settlement_close(strategy.safe_symbol, original.as_of)
    if (equity is None or safe is None or
            equity.currency != "GBP" or safe.currency != "GBP" or
            equity.price <= ZERO or safe.price <= ZERO or
            abs((equity.as_of - safe.as_of).days) > 1):
        raise ValidationError("Verified matching quarter-end GBP prices are required.")

    state = ledger_summary(strategy)
    positions = state["positions"]
    cash = state["cash"]
    if (cash < ZERO or any(q < ZERO for q in positions.values()) or
            any(q > ZERO for symbol, q in positions.items()
                if symbol not in (strategy.equity_symbol, strategy.safe_symbol))):
        raise ValidationError("Unreconciled cash or extra holdings prohibit a reset plan.")
    equity_qty = positions.get(strategy.equity_symbol, ZERO)
    safe_qty = positions.get(strategy.safe_symbol, ZERO)
    if equity_qty <= ZERO:
        raise ValidationError("A recorded positive growth holding is required.")

    eq_value = equity_qty * equity.price
    safe_value = safe_qty * safe.price
    total = eq_value + safe_value + cash
    if total <= ZERO:
        raise ValidationError("The existing portfolio cannot be valued.")
    difference = total * SLEEVE - eq_value
    actions = []
    if difference < -CENT:
        sell_qty = min(equity_qty, _floor_qty(-difference, equity.price))
        if sell_qty > ZERO:
            actions.append(_order("SELL", strategy.equity_symbol, sell_qty, equity.price))
        buy_qty = _floor_qty(cash + sell_qty * equity.price, safe.price)
        if buy_qty > ZERO:
            actions.append(_order("BUY", strategy.safe_symbol, buy_qty, safe.price))
    elif difference > CENT:
        safe_budget = min(safe_value, max(ZERO, difference - cash))
        sell_qty = min(safe_qty, _floor_qty(safe_budget, safe.price))
        if sell_qty > ZERO:
            actions.append(_order("SELL", strategy.safe_symbol, sell_qty, safe.price))
        buy_qty = _floor_qty(min(difference, cash + sell_qty * safe.price), equity.price)
        if buy_qty > ZERO:
            actions.append(_order("BUY", strategy.equity_symbol, buy_qty, equity.price))
    if not actions:
        raise ValidationError("The portfolio is already within the 60/40 rounding tolerance.")

    label = str(original.as_of)
    review = StrategySignal.objects.create(
        strategy=strategy, signal_key="owner-reset:" + original.signal_key,
        kind="QUARTERLY", as_of=original.as_of,
        title="Owner-selected 60/40 reset review — " + label,
        message=("Owner expressly chose to review a reset to 60% growth / "
                 "40% safe and cash after the empty-reserve advisory. "
                 "The normal v2 all-in advice was NOT changed. "
                 "This alternative uses recorded holdings and verified GBP "
                 "quarter-end closes (%s and %s). Every estimated quantity "
                 "requires actual manual fills and verified later GBP closes; "
                 "no broker orders have been placed.")
                % (equity.as_of, safe.as_of),
        actions=actions,
        reset_reason="Owner-elected 60/40 reset after exhausted-reserve review",
        strategy_version=NINE_SIG_RULE_VERSION,
    )
    # A deliberate selection replaces the displayed proposal, never deletes
    # or mutates its mathematical result or recorded action list.
    original.review_outcome = "NOT_APPLICABLE"
    original.review_note = (
        "Owner explicitly requested a separate manual 60/40 reset review: %s"
        % review.signal_key
    )
    original.acknowledged_at = timezone.now()
    original.save(update_fields=["review_outcome", "review_note", "acknowledged_at"])
    return review
