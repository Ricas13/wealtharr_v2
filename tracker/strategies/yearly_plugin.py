"""Plug-in wiring for fixed-weight strategies reviewed after each year (or month) end."""
from tracker.core.calendar import (
    month_end_before, month_end_on_or_after, quarter_end_before, quarter_end_on_or_after,
    year_end_before, year_end_on_or_after,
)
from tracker.strategies.base import StrategyModule

SCHEDULES = {
    # cadence word: (last review before a day, next review on/after a day, title)
    "yearly": (year_end_before, year_end_on_or_after, lambda d: "Yearly rebalance %d" % d.year),
    "monthly": (month_end_before, month_end_on_or_after, lambda d: "Monthly check %s" % d.strftime("%B %Y")),
    "quarterly": (quarter_end_before, quarter_end_on_or_after,
                  lambda d: "Quarterly rebalance %d-Q%d" % (d.year, (d.month - 1) // 3 + 1)),
}


def yearly_review(instance, *, key, rules, cadence="yearly"):
    """Hourly: refresh prices; after a year (or month) end, save that review once.

    ``rules`` is the strategy's pure versioned module: VERSION, WEIGHTS, Fund,
    Quote and evaluate(funds=, positions=, cash=, quotes=, as_of=).
    """
    from datetime import timedelta
    from decimal import Decimal

    from django.utils import timezone

    from tracker.market_data import TICKER, settlement_close, sync_ticker
    from tracker.models import PriceMark, StrategySignal
    from tracker.services import replay
    from tracker.strategies.instruments import role_symbols
    from tracker.v2_ledger_core import effective_entries

    if not instance.market_data_enabled:
        return {"status": "disabled"}
    if instance.strategy_version and instance.strategy_version != rules.VERSION:
        return {"status": "cannot_compute", "reason": "Pinned rule version differs; owner review required"}
    roles = role_symbols(instance)
    if not roles or not all(TICKER.fullmatch(s) for s in roles.values()):
        return {"status": "invalid symbol"}
    today = timezone.localdate()
    for symbol in roles.values():
        close = sync_ticker(symbol)
        # Dashboard marks: the latest verified GBP close, never a guess.
        if close and close.currency == instance.account.currency and close.as_of >= today - timedelta(days=8):
            previous = PriceMark.objects.filter(strategy=instance, symbol=symbol).first()
            if not previous or previous.as_of <= close.as_of:
                PriceMark.objects.update_or_create(
                    strategy=instance, symbol=symbol,
                    defaults={"price": close.price, "as_of": close.as_of, "source": "yahoo"})
    last_before, _, title = SCHEDULES[cadence]
    as_of = last_before(today)
    if instance.started_on and instance.started_on > as_of:
        return {"status": "waiting for the first review date"}
    signal_key = "%s:%s:%s" % (key, instance.pk, as_of.isoformat())
    if StrategySignal.objects.filter(strategy=instance, signal_key=signal_key).exists():
        return {"status": "ok"}
    entries = [e for e in effective_entries(instance) if e.occurred_on <= as_of]
    cash, _, positions = replay(entries)
    if not positions:
        return {"status": "waiting for first purchase"}
    weights = dict(rules.WEIGHTS)
    funds = tuple(rules.Fund(role, symbol, weights[role]) for role, symbol in roles.items() if role in weights)
    quotes = {}
    for fund in funds:
        close = settlement_close(fund.symbol, as_of)
        if close and close.currency == instance.account.currency:
            quotes[fund.symbol] = rules.Quote(fund.symbol, Decimal(close.price), close.as_of)
    result = rules.evaluate(funds=funds, positions=positions, cash=cash, quotes=quotes, as_of=as_of)
    if result.status == "cannot_compute":
        return {"status": "cannot_compute", "reason": result.reason}
    StrategySignal.objects.create(
        strategy=instance, signal_key=signal_key, kind="QUARTERLY", as_of=as_of,
        title=title(as_of), message=result.reason,
        actions=[t.as_action() for t in result.trades], strategy_version=rules.VERSION)
    return {"status": "ok"}


def yearly_rebalance_plugin(key, rules, *, study=None, cadence="yearly", by_version=None):
    last_before, next_on_or_after, _ = SCHEDULES[cadence]
    return StrategyModule(
        key=key,
        review=lambda instance: yearly_review(instance, key=key, rules=rules, cadence=cadence),
        next_review=next_on_or_after,
        last_review_before=last_before,
        allocation_label="Target",
        cadence_word=cadence,
        study=study,
        by_version=by_version or {},
    )
