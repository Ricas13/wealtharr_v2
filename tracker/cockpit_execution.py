"""Section G — one actual owner fill at a time; no broker integration.

This is a Django persistence adapter. Source strategy suggestions remain
immutable; only confirmed historical manual executions enter the ledger.
Partial fills carry no completed execution marker and NO reset baseline.
"""
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .instrument_mapping import for_strategy
from .models import (
    LedgerEntry, MarketObservation, RebalanceExecution, SignalFill,
    StrategyBaseline, StrategyInstance, StrategySignal,
)
from .services import ledger_summary, validate_and_record
from .strategies.instruments import symbols
from .strategies.registry import last_review_before, module_for

D = Decimal
ZERO = D("0")
ONE = D("1")
CENTS = D("0.01")



def verified_quote_evidence(marks):
    """Capture exact accepted GBP observation identities, not estimated prices."""
    result = {}
    for role, mark in zip(("growth", "safe"), marks):
        observation = MarketObservation.objects.filter(
            symbol=mark.symbol, as_of=mark.as_of, currency="GBP",
            price=mark.price).values_list("pk", flat=True).first()
        if observation is None:
            raise ValidationError("Missing persisted GBP price observation for the reset.")
        result[role] = {
            "observation_id": observation, "symbol": mark.symbol,
            "as_of": mark.as_of.isoformat(), "price_gbp": str(mark.price),
            "currency": "GBP", "price_mark_source": mark.source,
        }
    return result


def proposed_actions(signal):
    return tuple(a for a in signal.actions if a.get("side") in ("BUY", "SELL"))


def _linked(locked, signal):
    return list(SignalFill.objects.filter(strategy=locked, signal=signal)
                .select_related("ledger_entry").order_by("ledger_entry__occurred_on", "pk"))


def completion_rows(signal, fills):
    """A Decimal ledger reconciliation, not a price-based execution estimate."""
    actions = proposed_actions(signal)
    delivered = {index: ZERO for index in range(len(actions))}
    for item in fills:
        if not 0 <= item.action_index < len(actions):
            raise ValidationError("A recorded fill has an unknown proposed action.")
        entry = item.ledger_entry
        suggestion = actions[item.action_index]
        if (entry.strategy_id != item.strategy_id or
                item.signal_id != signal.pk or
                entry.symbol != suggestion["symbol"] or
                entry.kind != suggestion["side"]):
            raise ValidationError("A linked fill no longer matches its original action.")
        delivered[item.action_index] += entry.quantity
    result = []
    for index, suggestion in enumerate(actions):
        expected = D(str(suggestion["quantity"]))
        if not expected.is_finite() or expected <= ZERO:
            raise ValidationError("Invalid or missing immutable proposed quantity.")
        if delivered[index] > expected:
            raise ValidationError("Recorded fills exceed the original proposed quantity.")
        result.append({
            "index": index, "side": suggestion["side"],
            "symbol": suggestion["symbol"], "suggested": expected,
            "recorded": delivered[index],
            "remaining": expected - delivered[index],
            "complete": delivered[index] == expected,
            "fills": tuple(item for item in fills if item.action_index == index),
        })
    return tuple(result)


def late_review_allowed(strategy, signal):
    """A past quarter's review with recorded fills that nothing later has built on.

    It can still be finished against the fills already recorded (never new
    fills: trades after the quarter belong to the next review).
    """
    if signal.kind != "QUARTERLY" or signal.as_of >= last_review_before(strategy, timezone.localdate()):
        return False
    if not SignalFill.objects.filter(signal=signal, strategy=strategy).exists():
        return False
    later = StrategySignal.objects.filter(strategy=strategy, kind="QUARTERLY", as_of__gt=signal.as_of)
    return not (SignalFill.objects.filter(signal__in=later).exists()
                or RebalanceExecution.objects.filter(signal__in=later).exists())


def _check_current(locked, signal, *, allow_late=False):
    current = signal.as_of == last_review_before(locked, timezone.localdate())
    if (signal.strategy_id != locked.pk or signal.kind != "QUARTERLY" or
            not (current or (allow_late and late_review_allowed(locked, signal))) or
            signal.review_outcome in ("FALSE_POSITIVE", "NOT_APPLICABLE")):
        raise ValidationError("Only the current valid quarterly review can receive fills.")
    if RebalanceExecution.objects.filter(signal=signal).exists():
        raise ValidationError("This review has already been completed.")
    if for_strategy(locked) is None:
        raise ValidationError("Exact account instruments are not mapped.")
    # Pin the released strategy/mapping at enrolment. Unpinned old accounts
    # remain eligible via the exact legacy for_strategy compatibility route.
    if locked.strategy_version or locked.mapping_version:
        from .strategies.registry import describe_strategy
        from .instrument_mapping import MAPPING_VERSION
        described = describe_strategy(locked.preset.slug)
        if (not described or locked.strategy_version != described.version or
                locked.mapping_version != MAPPING_VERSION):
            raise ValidationError("Pinned strategy or instrument version differs from installed rules.")


def _external_after_signal(locked, signal, fills):
    """Trades or withdrawals since the review, other than its own fills.

    New money, dividends or interest after quarter-end don't change the
    quarter's trades, so they don't block recording them.
    """
    linked = {fill.ledger_entry_id for fill in fills}
    actual = set(locked.ledger.filter(occurred_on__gt=signal.as_of,
                                      kind__in=("BUY", "SELL", "WITHDRAW"))
                 .values_list("pk", flat=True))
    return actual != linked


def record_single_fill(*, owner, strategy_id, signal_id, action_index,
                       executed_on, quantity, unit_price, fee, idempotency_key):
    """Atomically append one *actual* fill, with idempotent retry.

    A fill can be recorded while the verified next-day market close is still
    unavailable. It is expressly PARTIAL/pending until the user separately
    completes all actions and post-fill valuation exists.
    """
    try:
        nonce = str(UUID(str(idempotency_key)))
        if UUID(nonce).version != 4:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Invalid confirmation token. Reload the review page.")
    if (not isinstance(action_index, int) or
            not isinstance(executed_on, type(timezone.localdate())) or
            any(not isinstance(x, D) or not x.is_finite() for x in (quantity, unit_price, fee)) or
            quantity <= ZERO or unit_price <= ZERO or fee < ZERO):
        raise ValidationError("Enter an actual date, positive units and price, and nonnegative GBP fees.")
    if executed_on > timezone.localdate():
        raise ValidationError("A future broker execution cannot be recorded.")
    with transaction.atomic():
        locked = StrategyInstance.objects.select_for_update().select_related(
            "account", "preset").get(pk=strategy_id, owner=owner)
        signal = StrategySignal.objects.select_for_update().get(
            pk=signal_id, strategy=locked)
        prior = SignalFill.objects.filter(
            signal=signal, idempotency_key=nonce).select_related("ledger_entry").first()
        if prior is not None:
            if (prior.action_index != action_index or
                    prior.ledger_entry.occurred_on != executed_on or
                    prior.ledger_entry.quantity != quantity or
                    prior.ledger_entry.unit_price != unit_price or
                    prior.ledger_entry.fee != fee):
                raise ValidationError("This confirmation token was already used for different details.")
            return "duplicate", prior
        _check_current(locked, signal)
        actions = proposed_actions(signal)
        # At most one trade per fund (9Sig's reviews have up to four).
        if not 0 <= action_index < len(actions) or not 1 <= len(actions) <= max(4, len(symbols(locked))):
            raise ValidationError("Select an action from the current review.")
        if executed_on <= signal.as_of:
            raise ValidationError("Your actual fill must be after the review date.")
        existing = _linked(locked, signal)
        if _external_after_signal(locked, signal, existing):
            raise ValidationError("Other portfolio activity since the review requires a fresh reconciliation.")
        rows = completion_rows(signal, existing)
        if quantity > rows[action_index]["remaining"]:
            raise ValidationError("This fill exceeds the remaining proposed units. Reconcile the larger trade separately.")
        action = actions[action_index]
        if action["symbol"] not in symbols(locked):
            raise ValidationError("The proposed broker symbol is not the mapped holding.")
        entry = LedgerEntry(
            kind=action["side"], occurred_on=executed_on,
            symbol=action["symbol"], amount=ZERO, quantity=quantity,
            unit_price=unit_price, fee=fee,
            note="Owner actual fill for quarterly review %s action %s" % (signal.pk, action_index + 1))
        entry.full_clean(exclude=["strategy"])
        validate_and_record(locked, entry)
        linked = SignalFill.objects.create(
            strategy=locked, signal=signal, ledger_entry=entry,
            action_index=action_index, idempotency_key=nonce,
            recorded_by=owner)
        return "recorded", linked


def finalize_manual_review(*, owner, strategy_id, signal_id):
    """Confirm COMPLETE only when all actual fills and next-day GBP closes exist.

    Exact end-state is checked inside the same locked transaction which writes
    the immutable baseline (reset ONLY) and the execution completion marker.
    """
    with transaction.atomic():
        locked = StrategyInstance.objects.select_for_update().select_related(
            "account", "preset").get(pk=strategy_id, owner=owner)
        signal = StrategySignal.objects.select_for_update().get(
            pk=signal_id, strategy=locked)
        _check_current(locked, signal, allow_late=True)
        fills = _linked(locked, signal)
        if not fills:
            raise ValidationError("Record at least one actual broker fill first.")
        rows = completion_rows(signal, fills)
        if not rows or not all(row["complete"] for row in rows):
            raise ValidationError("This review is still partial. Finish each proposed action first.")
        if _external_after_signal(locked, signal, fills):
            raise ValidationError("Other activity since the review requires reconciliation.")
        before = ledger_summary(locked)
        # Full completion needs a verified post-fill close rather than an
        # arbitrary legacy current mark. Later settlement cannot be invented.
        from .signal_status import _price_status
        fresh, why, marks = _price_status(locked, before)
        if not fresh:
            raise ValidationError(why)
        first_day = min(f.ledger_entry.occurred_on for f in fills)
        last_day = max(f.ledger_entry.occurred_on for f in fills)
        if any(m is None or m.as_of <= last_day for m in marks):
            raise ValidationError("Wait for a verified GBP close AFTER the latest real fill.")
        if len({m.as_of for m in marks}) != 1:
            raise ValidationError("Every held instrument needs the same dated GBP close.")
        if before["value"] is None or before["value"] <= ZERO or before["cash"] < ZERO:
            raise ValidationError("Portfolio valuation or cash is incomplete.")
        if any(s not in symbols(locked) for s in before["positions"]):
            raise ValidationError("Unmapped holdings cannot start a completed review.")
        # Fully recorded quantities from an ordinary review are exact;
        # unexpected additional entries since the review are rejected above.
        reset = bool(signal.reset_reason)
        baseline = None
        if reset:
            # A reset (60/40 baseline) exists only for strategies that define one.
            plugin = module_for(locked)
            if not (plugin and plugin.supports_reset):
                raise ValidationError("This strategy has no reset step.")
            actual_equity = before["positions"].get(locked.equity_symbol, ZERO) * marks[0].price
            actual_safe = before["positions"].get(locked.safe_symbol, ZERO) * marks[1].price
            if abs(actual_equity - before["value"] * D("0.60")) / before["value"] > D("0.05"):
                raise ValidationError("Actual post-fill equity is outside the reviewed reset tolerance.")
            if locked.baselines.filter(as_of__gt=signal.as_of).exists():
                raise ValidationError("A later confirmed baseline already exists.")
            baseline = StrategyBaseline.objects.create(
                strategy=locked, reset_signal=signal, as_of=marks[0].as_of,
                equity_value=actual_equity.quantize(CENTS),
                equity_quantity=before["positions"].get(locked.equity_symbol, ZERO),
                safe_value=actual_safe.quantize(CENTS),
                cash_value=before["cash"],
                strategy_version=locked.strategy_version,
                mapping_version=locked.mapping_version,
                quote_evidence=verified_quote_evidence(marks))
        completed = RebalanceExecution.objects.create(
            signal=signal, strategy=locked, executed_on=last_day,
            baseline=baseline)
        signal.review_outcome = "CONFIRMED"
        signal.acknowledged_at = timezone.now()
        signal.save(update_fields=["review_outcome", "acknowledged_at"])
        return completed
