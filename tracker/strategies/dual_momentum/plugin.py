"""Dual Momentum as a strategy plug-in: one review after each month end."""
from decimal import Decimal

from tracker.market_data import QUOTE_MAX_AGE
from tracker.core.calendar import month_end_before, month_end_on_or_after, months_before
from tracker.strategies.base import StrategyModule
from tracker.strategies.dual_momentum import v1
from tracker.study_fixed import Source
from tracker.study_momentum import MomentumStudy

KEY = "dual-momentum"
# Signal-only series for "cash" (absolute momentum), per account currency. Never
# held. Pounds: an accumulating SONIA fund (price = total return); dollars:
# 1-3 month T-bills, as in the original; euros: an accumulating €STR fund.
CASH_SYMBOLS = {"GBP": "CSH2.L", "USD": "BIL", "EUR": "XEON.DE"}
CASH_SYMBOL = CASH_SYMBOLS["GBP"]
# These USD lines distribute rather than accumulating the full cash return.
# No recorded income is not proof of a 0% distribution yield: do not issue a
# month-end allocation switch on incomplete Yahoo dividend history.
DISTRIBUTING_USD = frozenset(("BIL", "SPY", "ACWX"))


def cash_symbol(instance):
    return CASH_SYMBOLS.get(instance.account.currency, CASH_SYMBOL)


# The longest gap between payments of any distributing fund used here (ACWX pays
# twice a year). A window whose recorded payments leave a longer hole, or stop
# short of either end, is missing events: no switch is issued on it.
MAX_PAYMENT_GAP_DAYS = 200


def _income_complete(dates, begin, end):
    if not dates:
        return False
    if (dates[0] - begin).days > MAX_PAYMENT_GAP_DAYS or (end - dates[-1]).days > MAX_PAYMENT_GAP_DAYS:
        return False
    return all((b - a).days <= MAX_PAYMENT_GAP_DAYS for a, b in zip(dates, dates[1:]))


def _with_income(end, begin, currency):
    """The window's end price as a total return: each payment reinvested at the
    fund's own close on its ex-date.

    The published rule compares 12-month total returns. Distributing funds
    (BIL, SPY, ACWX) pay income out instead of growing the price. A window with
    missing or unpriceable payments returns None (on hold), never a guess.
    """
    from tracker.market_data import settlement_close
    from tracker.models import DividendEvent
    events = list(DividendEvent.objects.filter(
        symbol=end.symbol, currency=currency,
        ex_date__gt=begin.as_of, ex_date__lte=end.as_of,
    ).order_by("ex_date").values_list("ex_date", "amount"))
    if currency == "USD" and end.symbol in DISTRIBUTING_USD and not _income_complete(
            [day for day, _ in events], begin.as_of, end.as_of):
        return None  # Income history doesn't reach across the whole 12-month window.
    growth = Decimal("1")
    for day, amount in events:
        close = settlement_close(end.symbol, day, days=7)
        if close is None or close.currency != currency or close.price <= 0:
            return None  # Can't price the reinvestment: hold rather than approximate.
        growth *= 1 + Decimal(amount) / Decimal(close.price)
    return v1.Quote(end.symbol, end.price_gbp * growth, end.as_of) if events else end


def decide(instance, as_of, *, positions=None, cash=None):
    """Run the pure rules at month end ``as_of`` from verified closes. Read-only."""
    from tracker.market_data import period_close, settlement_close
    from tracker.services import replay
    from tracker.strategies.instruments import role_symbols
    from tracker.v2_ledger_core import effective_entries

    funds = role_symbols(instance)
    if positions is None:
        entries = [e for e in effective_entries(instance) if e.occurred_on <= as_of]
        cash, _, positions = replay(entries)
    start = months_before(as_of, v1.LOOKBACK_MONTHS)

    def quote(symbol, day):
        # The review date's close must be settled; the lookback start is long past.
        close = period_close(symbol, day) if day == as_of else settlement_close(symbol, day)
        if close and close.currency == instance.account.currency:
            return v1.Quote(symbol, Decimal(close.price), close.as_of)
        return None

    quotes = {s: q for s in funds.values() if (q := quote(s, as_of))}
    lookbacks = {}
    for key, symbol in (("us", funds.get("us")), ("intl", funds.get("intl")), ("cash", cash_symbol(instance))):
        begin, end = (quote(symbol, start), quote(symbol, as_of)) if symbol else (None, None)
        if begin and end:
            adjusted = _with_income(end, begin, instance.account.currency)
            if adjusted is not None:
                lookbacks[key] = v1.Lookback(begin, adjusted)
    return v1.evaluate(funds=funds, positions=positions, cash=cash, quotes=quotes,
                       lookbacks=lookbacks, as_of=as_of, window_start=start)


def review(instance):
    """Hourly: refresh prices; after a month end, save that month's review once."""

    from django.utils import timezone

    from tracker.market_data import TICKER, sync_ticker
    from tracker.models import PriceMark, StrategySignal
    from tracker.strategies.instruments import role_symbols

    if not instance.market_data_enabled:
        return {"status": "disabled"}
    if instance.strategy_version and instance.strategy_version != v1.VERSION:
        return {"status": "cannot_compute", "reason": "Pinned rule version differs; owner review required"}
    roles = role_symbols(instance)
    if not roles or not all(TICKER.fullmatch(s) for s in roles.values()):
        return {"status": "invalid symbol"}
    today = timezone.localdate()
    for symbol in tuple(roles.values()) + (cash_symbol(instance),):
        close = sync_ticker(symbol)
        if symbol == cash_symbol(instance):
            continue
        if close and close.currency == instance.account.currency and close.as_of >= today - QUOTE_MAX_AGE:
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


STUDY = {
    "GBP": MomentumStudy(title="Dual Momentum", rules=v1, sources={
        "us": Source("VUAG.L", "US shares (VUAG)", "SPY", "SPY"),
        "intl": Source("XUSE.L", "world ex-US shares (XUSE)", "VEA", "VEA"),
        "bonds": Source("VAGS.L", "hedged global bonds (VAGS)", "AGG", "AGG in US dollars", proxy_in_gbp=False),
        "cash": Source("CSH2.L", "sterling cash (CSH2)", "BIL", "US T-bills (BIL) in US dollars",
                       proxy_in_gbp=False),
    }),
    "USD": MomentumStudy(title="Dual Momentum", currency="USD", rules=v1, sources={
        "us": Source("SPY", "US shares (SPY)"),
        "intl": Source("ACWX", "world ex-US shares (ACWX)", "EFA", "EFA (developed markets ex-US)"),
        "bonds": Source("AGG", "US aggregate bonds (AGG)"),
        "cash": Source("BIL", "T-bills (BIL)"),
    }),
    "EUR": MomentumStudy(title="Dual Momentum", currency="EUR", rules=v1, sources={
        "us": Source("SXR8.DE", "US shares (SXR8)", "SPY", "SPY"),
        "intl": Source("EXUS.DE", "world ex-US shares (EXUS)", "EFA", "EFA"),
        "bonds": Source("VAGF.DE", "hedged global bonds (VAGF)", "AGG", "AGG in US dollars", proxy_in_gbp=False),
        "cash": Source("XEON.DE", "euro cash (XEON)"),
    }),
}

DUAL_MOMENTUM = StrategyModule(
    key=KEY,
    review=review,
    next_review=month_end_on_or_after,
    last_review_before=month_end_before,
    reference_symbols=lambda instance: (cash_symbol(instance),),
    allocation_label="Chosen",
    cadence_word="monthly",
    current_weights=current_weights,
    study=STUDY,
)
