"""Dual Momentum as a strategy plug-in: one review after each month end."""
from decimal import Decimal

from tracker.core.calendar import month_end_before, month_end_on_or_after, months_before
from tracker.strategies.base import StrategyModule
from tracker.strategies.dual_momentum import v1
from tracker.study_fixed import Source
from tracker.study_momentum import MomentumStudy

KEY = "dual-momentum"
# Signal-only series for "cash" (absolute momentum): an accumulating sterling
# overnight-rate fund, so its price is a total return. Never held.
CASH_SYMBOL = "CSH2.L"


def decide(instance, as_of, *, positions=None, cash=None):
    """Run the pure rules at month end ``as_of`` from verified closes. Read-only."""
    from tracker.market_data import settlement_close
    from tracker.services import replay
    from tracker.strategies.instruments import role_symbols
    from tracker.v2_ledger_core import effective_entries

    funds = role_symbols(instance)
    if positions is None:
        entries = [e for e in effective_entries(instance) if e.occurred_on <= as_of]
        cash, _, positions = replay(entries)
    start = months_before(as_of, v1.LOOKBACK_MONTHS)

    def quote(symbol, day):
        close = settlement_close(symbol, day)
        if close and close.currency == "GBP":
            return v1.Quote(symbol, Decimal(close.price), close.as_of)
        return None

    quotes = {s: q for s in funds.values() if (q := quote(s, as_of))}
    lookbacks = {}
    for key, symbol in (("us", funds.get("us")), ("intl", funds.get("intl")), ("cash", CASH_SYMBOL)):
        begin, end = (quote(symbol, start), quote(symbol, as_of)) if symbol else (None, None)
        if begin and end:
            lookbacks[key] = v1.Lookback(begin, end)
    return v1.evaluate(funds=funds, positions=positions, cash=cash, quotes=quotes,
                       lookbacks=lookbacks, as_of=as_of, window_start=start)


def review(instance):
    """Hourly: refresh prices; after a month end, save that month's review once."""
    from datetime import timedelta

    from django.utils import timezone

    from tracker.market_data import TICKER, sync_ticker
    from tracker.models import PriceMark, StrategySignal
    from tracker.strategies.instruments import role_symbols

    if not instance.market_data_enabled or instance.account.currency != "GBP":
        return {"status": "disabled"}
    if instance.strategy_version and instance.strategy_version != v1.VERSION:
        return {"status": "cannot_compute", "reason": "Pinned rule version differs; owner review required"}
    roles = role_symbols(instance)
    if not roles or not all(TICKER.fullmatch(s) for s in roles.values()):
        return {"status": "invalid symbol"}
    today = timezone.localdate()
    for symbol in tuple(roles.values()) + (CASH_SYMBOL,):
        close = sync_ticker(symbol)
        if symbol == CASH_SYMBOL:
            continue
        if close and close.currency == "GBP" and close.as_of >= today - timedelta(days=8):
            previous = PriceMark.objects.filter(strategy=instance, symbol=symbol).first()
            if not previous or previous.as_of <= close.as_of:
                PriceMark.objects.update_or_create(
                    strategy=instance, symbol=symbol,
                    defaults={"price": close.price, "as_of": close.as_of, "source": "yahoo"})
    as_of = month_end_before(today)
    if instance.started_on and instance.started_on > as_of:
        return {"status": "waiting for the first month end"}
    key = "%s:%s:%s" % (KEY, instance.pk, as_of.isoformat())
    if StrategySignal.objects.filter(strategy=instance, signal_key=key).exists():
        return {"status": "ok"}
    result = decide(instance, as_of)
    if result.status == "cannot_compute":
        return {"status": "cannot_compute", "reason": result.reason}
    StrategySignal.objects.create(
        strategy=instance, signal_key=key, kind="QUARTERLY", as_of=as_of,
        title="Monthly review %s" % as_of.strftime("%B %Y"), message=result.reason,
        actions=[t.as_action() for t in result.trades], strategy_version=v1.VERSION)
    return {"status": "ok"}


def current_weights(instance):
    """{symbol: weight}: 100% in the fund the latest month end chose, or None."""
    from django.utils import timezone
    from tracker.strategies.instruments import role_symbols
    result = decide(instance, month_end_before(timezone.localdate()))
    chosen = dict(result.audit).get("chosen")
    if result.status == "cannot_compute" or not chosen:
        return None
    return {symbol: (Decimal("1") if role == chosen else Decimal("0"))
            for role, symbol in role_symbols(instance).items()}


STUDY = MomentumStudy(title="Dual Momentum", rules=v1, sources={
    "us": Source("VUAG.L", "US shares (VUAG)", "SPY", "SPY"),
    "intl": Source("XUSE.L", "world ex-US shares (XUSE)", "VEA", "VEA"),
    "bonds": Source("VAGS.L", "hedged global bonds (VAGS)", "AGG", "AGG in US dollars", proxy_in_gbp=False),
    "cash": Source(CASH_SYMBOL, "sterling cash (CSH2)", "BIL", "US T-bills (BIL) in US dollars",
                   proxy_in_gbp=False),
})

DUAL_MOMENTUM = StrategyModule(
    key=KEY,
    review=review,
    next_review=month_end_on_or_after,
    last_review_before=month_end_before,
    reference_symbols=(CASH_SYMBOL,),
    allocation_label="Chosen",
    cadence_word="monthly",
    current_weights=current_weights,
    study=STUDY,
)
