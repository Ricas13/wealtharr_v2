"""Nasdaq trend as an ordinary Wealtharr strategy plug-in.

Daily advisory signals use the *observed* US SPY/QQQ 200-session chart closes.
Prices for holdings are the pinned, separately verified trading lines. The
broker is never contacted and Wealtharr never assumes execution of proposals.
"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN

from django.db import transaction
from django.utils import timezone

from tracker.market_data import sync_ticker
from tracker.models import (
    MarketObservation, NasdaqTrendState, PriceMark, RebalanceExecution,
    SignalFill, StrategySignal, YahooSync,
)
from tracker.services import ledger_summary
from tracker.strategies.base import StrategyModule
from tracker.strategies.instruments import role_symbols
from tracker.strategies.nasdaq_trend_dca import v1
from tracker.study_trend_dca import TrendDcaStudy
from tracker.v2_ledger_core import effective_entries, replay

KEY = "nasdaq-trend-dca"
ZERO = Decimal("0")
CENT = Decimal("0.01")
SHARE = Decimal("0.0001")


def last_review_before(day):
    """Previous completed weekday, only for generic page calendar labels.

    Actual strategy reviews always use verified US trading-session closes,
    not an assumed weekday trading calendar.
    """
    previous = day - timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= timedelta(days=1)
    return previous


def next_review(day):
    candidate = day
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _verified_history():
    """Full sorted, synchronized US market sessions; reference chart closes.

    A daily quotation from StudyPrice is NEVER usable for live advice.
    Incomplete daily bars, source/quote-unit ambiguities, feed revisions,
    sparse histories, and stale or mismatched QQQ/SPY dates must fail closed.
    """
    for symbol in ("SPY", "QQQ"):
        state = YahooSync.objects.filter(symbol=symbol).first()
        if state is None or state.last_error or state.last_success is None:
            return None
    observations = {}
    for symbol in ("SPY", "QQQ"):
        records = list(MarketObservation.objects.filter(
            symbol=symbol, currency="USD", source="yahoo",
            raw_unit_verified=True, raw_quote_unit="USD",
        ).order_by("as_of").values_list("as_of", "raw_close"))
        if len(records) < 200 or any(price is None or price <= ZERO for _, price in records):
            return None
        observations[symbol] = dict(records)
    spy, qqq = observations["SPY"], observations["QQQ"]
    # Even one missing session invalidates a 200-day simple moving average.
    latest_spy = sorted(spy)
    latest_qqq = sorted(qqq)
    if latest_spy[-200:] != latest_qqq[-200:]:
        return None
    days = [day for day in latest_spy if day in qqq]
    if len(days) < 200 or any((b-a).days > 5 for a, b in zip(days[-200:], days[-199:])):
        return None
    if days[-1] < timezone.localdate() - timedelta(days=6):
        return None
    return tuple(v1.Close(day, spy[day], qqq[day]) for day in days)


def _held_quotes(instance):
    from tracker.core.catalogue import expected_quote_units
    roles = role_symbols(instance)
    if set(roles) != {"leveraged", "unleveraged"}:
        return None
    quotes = {}
    for role, symbol in roles.items():
        state = YahooSync.objects.filter(symbol=symbol).first()
        quote = MarketObservation.objects.filter(
            symbol=symbol, currency=instance.account.currency,
            source="yahoo", raw_unit_verified=True,
        ).order_by("-as_of").first()
        if (state is None or state.last_error or quote is None or quote.price <= ZERO
                or not quote.price.is_finite()
                or quote.as_of < timezone.localdate() - timedelta(days=8)
                or quote.raw_quote_unit not in expected_quote_units().get(symbol, ())):
            return None
        quotes[role] = quote
        old = PriceMark.objects.filter(strategy=instance, symbol=symbol).first()
        if old is None or old.as_of <= quote.as_of:
            PriceMark.objects.update_or_create(
                strategy=instance, symbol=symbol,
                defaults={"price": quote.price, "as_of": quote.as_of, "source": "yahoo"},
            )
    if abs((quotes["leveraged"].as_of - quotes["unleveraged"].as_of).days) > 1:
        return None
    return quotes


def _pending(instance):
    """At most one unresolved manual instruction. No overlapping new orders."""
    return (StrategySignal.objects.filter(strategy=instance, kind="QUARTERLY")
            .exclude(review_outcome__in=("CONFIRMED", "FALSE_POSITIVE", "NOT_APPLICABLE"))
            .exclude(actions=[]).exclude(execution__isnull=False)
            .order_by("-as_of", "-pk").first())


def _reconcile_dca(instance, row):
    """Count only COMPLETED, linked manual DCA buys in this OFF cycle."""
    if row.regime != "OFF" or row.off_since is None:
        return
    buys = (StrategySignal.objects.filter(
        strategy=instance, as_of__gte=row.off_since, kind="QUARTERLY",
        execution__isnull=False,
    ).order_by("as_of", "pk"))
    counted = 0
    for signal in buys:
        for action in signal.actions:
            if action.get("dca_installments"):
                # A completed review has real, reconciled SignalFill rows.
                if not SignalFill.objects.filter(signal=signal, ledger_entry__kind="BUY",
                                                 ledger_entry__symbol=action.get("symbol")).exists():
                    continue
                counted += int(action["dca_installments"])
    row.completed_instalments = min(12, counted)
    if row.reserve > ZERO:
        return
    # Cash becomes an eligible reserve only following a verified exit sale, or
    # an explicitly entered cash-only opening. Deposits added later never
    # inflate this fixed reserve.
    confirmed_sells = (SignalFill.objects.filter(
        strategy=instance, signal__as_of__gte=row.off_since,
        signal__execution__isnull=False, ledger_entry__kind="SELL",
    ).select_related("ledger_entry"))
    proceeds = sum((fill.ledger_entry.quantity * fill.ledger_entry.unit_price
                    - fill.ledger_entry.fee for fill in confirmed_sells), ZERO)
    if proceeds > ZERO:
        row.reserve = proceeds.quantize(CENT)
        row.reserve_source = "confirmed_exit_fills"
    else:
        entries = effective_entries(instance)
        _, _, positions = replay(entries)
        if not any(qty > ZERO for qty in positions.values()):
            cash = ledger_summary(instance)["cash"]
            if cash > ZERO and row.completed_instalments == 0:
                row.reserve = cash.quantize(CENT)
                row.reserve_source = "confirmed_cash_only"


def _trades(decision, roles, quotes, positions, cash, reserve):
    """Return manual trade suggestions; all amounts use vetted *local* closes."""
    trades = []
    held = {role: positions.get(symbol, ZERO) for role, symbol in roles.items()}
    if any(qty < ZERO for qty in held.values()) or cash < ZERO:
        return ()
    def sell(role):
        qty = held[role]
        if qty <= ZERO:
            return
        price = quotes[role].price
        estimate = (qty * price).quantize(CENT)
        if estimate < CENT:
            return
        trades.append({"side": "SELL", "symbol": roles[role],
                       "quantity": str(qty), "estimated_gbp": str(estimate)})
    if decision.target == "DCA":
        # Sell risk positions first. Never sell/rebalance previous DCA buys.
        sell("leveraged")
        if trades:
            return tuple(trades)
        if reserve <= ZERO or decision.dca_installments_due <= 0:
            return ()
        cash_to_use = min(cash, (reserve * v1.DCA_FRACTION *
                                decision.dca_installments_due).quantize(CENT))
        if cash_to_use < CENT:
            return ()
        role = "unleveraged"
        qty = (cash_to_use / quotes[role].price).quantize(SHARE, rounding=ROUND_DOWN)
        cost = (qty * quotes[role].price).quantize(CENT)
        if qty > ZERO and cost > ZERO:
            trades.append({"side": "BUY", "symbol": roles[role],
                           "quantity": str(qty), "estimated_gbp": str(cost),
                           "dca_installments": decision.dca_installments_due})
        return tuple(trades)

    target = {"LEVERAGED": "leveraged", "UNLEVERAGED": "unleveraged"}.get(decision.target)
    for role in roles:
        if role != target:
            sell(role)
    if target:
        budget = cash + sum((Decimal(t["estimated_gbp"]) for t in trades), ZERO)
        if budget >= CENT:
            qty = (budget / quotes[target].price).quantize(SHARE, rounding=ROUND_DOWN)
            cost = (qty * quotes[target].price).quantize(CENT)
            if qty > ZERO and cost > ZERO:
                trades.append({"side": "BUY", "symbol": roles[target],
                               "quantity": str(qty), "estimated_gbp": str(cost)})
    return tuple(trades)


def _state_decision(history, row, eligible):
    """Replay every unprocessed, verified session so crossings aren't skipped."""
    eligible_history = tuple(point for point in history if point.day <= eligible)
    if len(eligible_history) < 200:
        return None
    previous = v1.State(row.regime, row.off_since, row.guard)
    if row.last_as_of is None:
        # On first enrolment, seed the regime from history but do not create
        # imaginary past DCA fills, reserves or owner trades.
        state = v1.State()
        for i in range(199, len(eligible_history)):
            outcome = v1.decide(eligible_history[:i+1], state)
            if outcome.status == "ok":
                state = outcome.state
        if state.regime == "OFF":
            state = v1.State("OFF", eligible, "NONE")
        previous = state
    else:
        new = [point for point in eligible_history if point.day > row.last_as_of]
        if len(new) > 6:
            return None  # Too many unchecked trading sessions; no silently skipped crossings.
        state = previous
        for point in new:
            i = next(j for j in range(199, len(eligible_history)) if eligible_history[j].day == point.day)
            outcome = v1.decide(eligible_history[:i+1], state)
            if outcome.status != "ok":
                return None
            state = outcome.state
        previous = state
    decision = v1.decide(eligible_history, previous,
                         filled_dca_installments=(row.completed_instalments
                           if previous.regime == "OFF" and previous.off_since == row.off_since else 0))
    return decision if decision.status == "ok" else None


def review(instance):
    """Hourly review: no new actionable signal until all evidence is ready."""
    if not instance.market_data_enabled or instance.tracking_only:
        return {"status": "disabled"}
    if instance.strategy_version != v1.VERSION:
        return {"status": "cannot_compute", "reason": "Pinned rules changed."}
    roles = role_symbols(instance)
    if set(roles) != {"leveraged", "unleveraged"}:
        return {"status": "cannot_compute", "reason": "Trading line mapping unavailable."}
    for symbol in (*roles.values(), "SPY", "QQQ"):
        if sync_ticker(symbol) is None:
            return {"status": "cannot_compute", "reason": "Verified Yahoo data unavailable."}
    history = _verified_history()
    quotes = _held_quotes(instance)
    if not history or not quotes:
        return {"status": "cannot_compute", "reason": "Price history or quote evidence incomplete."}
    price_day = min(quote.as_of for quote in quotes.values())
    # The SPY close happens AFTER the London cash market shut. For GBP
    # portfolios only a subsequent London close can price instructions.
    eligible = min(history[-1].day,
                   price_day - timedelta(days=1) if instance.account.currency == "GBP" else price_day)
    if eligible < history[199].day:
        return {"status": "cannot_compute", "reason": "Insufficient complete US market sessions."}
    if eligible < timezone.localdate() - timedelta(days=7):
        return {"status": "cannot_compute", "reason": "No settled execution-market session after US signal."}
    with transaction.atomic():
        row, _ = NasdaqTrendState.objects.select_for_update().get_or_create(strategy=instance)
        # Never overwrite existing proposals as markets move. Reconcile first.
        if _pending(instance):
            return {"status": "waiting", "reason": "Record or dismiss the previous proposed trades."}
        _reconcile_dca(instance, row)
        decision = _state_decision(history, row, eligible)
        if decision is None:
            return {"status": "cannot_compute", "reason": "Unreconciled or incomplete daily signal history."}
        report = ledger_summary(instance)
        positions = report["positions"]
        if (report["cash"] < ZERO or any(q < ZERO for q in positions.values())
                or any(symbol not in roles.values() for symbol, q in positions.items() if q > ZERO)):
            return {"status": "cannot_compute", "reason": "Unreconciled holdings or cash."}
        old_off = row.off_since
        # A new OFF cycle must start without a fictitious rollover reserve.
        if decision.state.regime == "OFF" and decision.state.off_since != old_off:
            row.reserve = ZERO
            row.reserve_source = ""
            row.completed_instalments = 0
            _reconcile_dca(instance, row)
        if decision.state.regime == "ON":
            row.reserve = ZERO
            row.reserve_source = ""
            row.completed_instalments = 0
        trades = _trades(decision, roles, quotes, positions, report["cash"], row.reserve)
        if trades and (row.last_as_of != eligible or row.last_target != decision.target):
            StrategySignal.objects.get_or_create(
                strategy=instance,
                signal_key="nasdaq-trend:%s:%s" % (instance.pk, eligible.isoformat()),
                defaults={
                    "kind": "QUARTERLY",  # generic manual fill workflow uses this kind
                    "as_of": eligible,
                    "title": "Daily Nasdaq trend review — " + eligible.isoformat(),
                    "message": (decision.reason +
                                " Use the next available market session and confirm actual prices. "
                                "No broker orders have been placed."),
                    "actions": list(trades),
                    "strategy_version": v1.VERSION,
                },
            )
        row.last_as_of = eligible
        row.regime = decision.state.regime
        row.guard = decision.state.guard
        row.off_since = decision.state.off_since
        row.last_target = decision.target
        row.save()
    return {"status": "ok"}


def current_weights(instance):
    """For the normal Add Strategy/Today opening buy, without inventing prices."""
    history = _verified_history()
    if not history:
        return None
    row = NasdaqTrendState.objects.filter(strategy=instance).first()
    state = v1.State(row.regime, row.off_since, row.guard) if row else v1.State()
    if not row:
        for i in range(199, len(history)):
            attempt = v1.decide(history[:i+1], state)
            if attempt.status == "ok":
                state = attempt.state
    decision = v1.decide(history, state)
    if decision.status != "ok":
        return None
    roles = role_symbols(instance)
    if decision.target == "LEVERAGED":
        return {roles["leveraged"]: Decimal("1"), roles["unleveraged"]: ZERO}
    if decision.target == "UNLEVERAGED":
        return {roles["leveraged"]: ZERO, roles["unleveraged"]: Decimal("1")}
    return {roles["leveraged"]: ZERO, roles["unleveraged"]: ZERO}


STUDY = {
    "GBP": TrendDcaStudy(currency="GBP", leveraged="LQQ3.L", unleveraged="EQQQ.L"),
    "USD": TrendDcaStudy(currency="USD", leveraged="TQQQ", unleveraged="QQQ"),
}

NASDAQ_TREND_DCA = StrategyModule(
    key=KEY,
    review=review,
    next_review=next_review,
    last_review_before=last_review_before,
    reference_symbols=("SPY", "QQQ"),
    cadence_word="daily",
    allocation_label="Current rule",
    current_weights=current_weights,
    study=STUDY,
)
