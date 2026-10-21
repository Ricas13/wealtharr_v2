"""All Weather as a strategy plug-in: one review after each year end."""
from tracker.core.calendar import year_end_before, year_end_on_or_after
from tracker.strategies.base import StrategyModule

KEY = "all-weather"


def review(instance):
    """Hourly: refresh prices; after a year end, save that year's review once."""
    from datetime import timedelta
    from decimal import Decimal

    from django.utils import timezone

    from tracker.market_data import TICKER, settlement_close, sync_ticker
    from tracker.models import PriceMark, StrategySignal
    from tracker.services import replay
    from tracker.strategies.all_weather import v1
    from tracker.strategies.instruments import role_symbols
    from tracker.v2_ledger_core import effective_entries

    if not instance.market_data_enabled or instance.account.currency != "GBP":
        return {"status": "disabled"}
    if instance.strategy_version and instance.strategy_version != v1.VERSION:
        return {"status": "cannot_compute", "reason": "Pinned rule version differs; owner review required"}
    roles = role_symbols(instance)
    if not roles or not all(TICKER.fullmatch(s) for s in roles.values()):
        return {"status": "invalid symbol"}
    today = timezone.localdate()
    for symbol in roles.values():
        close = sync_ticker(symbol)
        # Dashboard marks: the latest verified GBP close, never a guess.
        if close and close.currency == "GBP" and close.as_of >= today - timedelta(days=8):
            previous = PriceMark.objects.filter(strategy=instance, symbol=symbol).first()
            if not previous or previous.as_of <= close.as_of:
                PriceMark.objects.update_or_create(
                    strategy=instance, symbol=symbol,
                    defaults={"price": close.price, "as_of": close.as_of, "source": "yahoo"})
    as_of = year_end_before(today)
    if instance.started_on and instance.started_on > as_of:
        return {"status": "waiting for the first year end"}
    key = "all-weather:%s:%s" % (instance.pk, as_of.isoformat())
    if StrategySignal.objects.filter(strategy=instance, signal_key=key).exists():
        return {"status": "ok"}
    entries = [e for e in effective_entries(instance) if e.occurred_on <= as_of]
    cash, _, positions = replay(entries)
    if not positions:
        return {"status": "waiting for first purchase"}
    weights = dict(v1.WEIGHTS)
    funds = tuple(v1.Fund(role, symbol, weights[role]) for role, symbol in roles.items() if role in weights)
    quotes = {}
    for fund in funds:
        close = settlement_close(fund.symbol, as_of)
        if close and close.currency == "GBP":
            quotes[fund.symbol] = v1.Quote(fund.symbol, Decimal(close.price), close.as_of)
    result = v1.evaluate(funds=funds, positions=positions, cash=cash, quotes=quotes, as_of=as_of)
    if result.status == "cannot_compute":
        return {"status": "cannot_compute", "reason": result.reason}
    StrategySignal.objects.create(
        strategy=instance, signal_key=key, kind="QUARTERLY", as_of=as_of,
        title="Yearly rebalance %d" % as_of.year, message=result.reason,
        actions=[t.as_action() for t in result.trades], strategy_version=v1.VERSION)
    return {"status": "ok"}


ALL_WEATHER = StrategyModule(
    key=KEY,
    review=review,
    next_review=year_end_on_or_after,
    last_review_before=year_end_before,
    allocation_label="Target",
)
