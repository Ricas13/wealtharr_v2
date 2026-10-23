"""Nasdaq Trend + DCA in Wealtharr's ordinary strategy lifecycle.

Signals use completed US sessions; manual fills use the exact mapped
GBP/USD trading line. No broker connection, execution or invented price.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from tracker.strategies.base import StrategyModule
from tracker.strategies.nasdaq_trend_dca import v1
from tracker.strategies.instruments import role_symbols

KEY = "nasdaq-trend-dca"
CURRENT_DAYS = 7   # a proposal stays actionable (fills can be recorded) this long
REFRESH_DAYS = 5   # an unchanged pending instruction is kept, not re-issued
MAX_GAP_DAYS = 6   # longest calendar gap in a trustworthy daily series
ZERO = Decimal("0")


def last_review_before(day):
    """Latest *observed* completed US session, including holidays/weekends."""
    from tracker.models import MarketObservation
    return (MarketObservation.objects.filter(symbol="SPY", currency="USD",
            as_of__lte=day).order_by("-as_of")
            .values_list("as_of", flat=True).first() or day - timedelta(days=1))


def current_review_floor(day):
    """A proposal stays actionable for a week, so fills can be recorded after
    the next session's close has already been fetched."""
    return day - timedelta(days=CURRENT_DAYS)


def next_review(day):
    """Informational next weekday; actual reviews require settled US closes."""
    at = day + timedelta(days=1)
    while at.weekday() > 4:
        at += timedelta(days=1)
    return at


def _signal(as_of):
    """(signal, problem): the trend state on ``as_of`` from the full SPY/QQQ
    history, or why it can't be trusted."""
    from tracker.models import MarketObservation, YahooSync
    if YahooSync.objects.filter(symbol__in=("SPY", "QQQ"),
                                last_error__startswith="PRICE_HOLD:").exists():
        return None, "SPY or QQQ prices are on hold."
    series = {}
    for symbol in ("SPY", "QQQ"):
        series[symbol] = list(MarketObservation.objects.filter(
            symbol=symbol, currency="USD", as_of__lte=as_of,
            price__gt=0).order_by("as_of").values_list("as_of", "price"))
    shared = sorted({d for d, _ in series["SPY"]} & {d for d, _ in series["QQQ"]})
    window = shared[-v1.WINDOW:]
    if len(window) < v1.WINDOW:
        return None, "Fewer than 200 shared SPY and QQQ closes are stored."
    if window[-1] != as_of:
        return None, "SPY and QQQ do not have a matching completed close."
    if any((b - a).days > MAX_GAP_DAYS for a, b in zip(window, window[1:])):
        return None, "The last 200 SPY/QQQ closes have gaps; the average can't be trusted."
    signal = v1.trend_at(series["SPY"], series["QQQ"], as_of)
    if signal is None:
        return None, "SPY has not yet crossed either band, so the state is unknown."
    return signal, ""


def _cash_flows(instance, signal, funds, entries):
    """Dated cash added since the SPY exit, and what 1x Nasdaq cost since."""
    from tracker.services import replay
    if signal.regime != "off" or not signal.exit_on:
        return (), ZERO
    exit_on = signal.exit_on
    base, _, _ = replay([e for e in entries if e.occurred_on <= exit_on])
    inflows = [(exit_on, base)] if base > ZERO else []
    spent = ZERO
    for e in entries:
        if e.occurred_on <= exit_on:
            continue
        if e.kind in ("DEPOSIT", "DIVIDEND", "INTEREST"):
            inflows.append((e.occurred_on, e.amount))
        elif e.kind == "SELL":
            inflows.append((e.occurred_on, e.quantity * e.unit_price - e.fee))
        elif e.kind == "BUY" and e.symbol == funds["unleveraged"]:
            spent += e.quantity * e.unit_price + e.fee
    return tuple(inflows), spent


def decide(instance, as_of, *, signal=None):
    from tracker.market_data import settlement_close
    from tracker.models import YahooSync
    from tracker.services import replay
    from tracker.v2_ledger_core import effective_entries

    funds = role_symbols(instance)
    if signal is None:
        signal, problem = _signal(as_of)
        if signal is None:
            return v1.Result(v1.VERSION, "cannot_compute", "REFERENCE", problem)
    if YahooSync.objects.filter(symbol__in=tuple(funds.values()),
                                last_error__startswith="PRICE_HOLD:").exists():
        return v1.Result(v1.VERSION, "cannot_compute", "PRICE_HOLD",
                         "A mapped instrument's price is on hold.")
    entries = [e for e in effective_entries(instance) if e.occurred_on <= as_of]
    cash, _, positions = replay(entries)
    quotes = {}
    for symbol in funds.values():
        close = settlement_close(symbol, as_of, days=5)
        if close and close.currency == instance.account.currency and close.price > 0:
            quotes[symbol] = v1.Quote(symbol, Decimal(close.price), close.as_of)
    inflows, spent = _cash_flows(instance, signal, funds, entries)
    return v1.evaluate(funds=funds, positions=positions, cash=cash, quotes=quotes,
                       signal=signal, inflows=inflows, dca_spent=spent)


def _same_orders(prior, result):
    return ([(a.get("side"), a.get("symbol")) for a in prior.actions] ==
            [(t.side, t.symbol) for t in result.trades])


def review(instance):
    """The existing hourly command invokes this plug-in for every live account."""
    from tracker.market_data import TICKER, sync_ticker
    from tracker.models import PriceMark, RebalanceExecution, SignalFill, StrategySignal

    if not instance.market_data_enabled:
        return {"status": "disabled"}
    if instance.strategy_version != v1.VERSION:
        return {"status": "cannot_compute", "reason": "Pinned rule version differs."}
    funds = role_symbols(instance)
    if set(funds) != set(v1.ROLES) or not all(TICKER.fullmatch(s) for s in funds.values()):
        return {"status": "cannot_compute", "reason": "Missing verified mapped funds."}

    # Sync reference series first: these are USD signals, never holdings.
    for ref in ("SPY", "QQQ"):
        sync_ticker(ref)
    today = timezone.localdate()
    for symbol in funds.values():
        close = sync_ticker(symbol)
        if close and close.currency == instance.account.currency and close.as_of >= today - timedelta(days=8):
            previous = PriceMark.objects.filter(strategy=instance, symbol=symbol).first()
            if not previous or previous.as_of <= close.as_of:
                PriceMark.objects.update_or_create(
                    strategy=instance, symbol=symbol,
                    defaults={"price": close.price, "as_of": close.as_of, "source": "yahoo"})
    as_of = last_review_before(today)
    if instance.started_on and as_of < instance.started_on:
        return {"status": "waiting for the first session"}
    if as_of < today - timedelta(days=8):
        return {"status": "cannot_compute", "reason": "No fresh settled 200-session signal."}
    signal, problem = _signal(as_of)
    if signal is None:
        return {"status": "cannot_compute", "reason": problem}
    result = decide(instance, as_of, signal=signal)
    if result.status == "cannot_compute":
        return {"status": "cannot_compute", "reason": result.reason}

    prior = StrategySignal.objects.filter(
        strategy=instance, kind="QUARTERLY",
    ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("-as_of", "-pk").first()
    if prior and prior.actions and not RebalanceExecution.objects.filter(signal=prior).exists():
        if SignalFill.objects.filter(signal=prior).exists():
            # Fills recorded, awaiting the next verified close to finish.
            return {"status": "cannot_compute",
                    "reason": "Finish the earlier partial manual review before a new one."}
        if prior.as_of < as_of:
            if (result.status == "action_needed" and _same_orders(prior, result)
                    and prior.as_of >= as_of - timedelta(days=REFRESH_DAYS)):
                return {"status": "ok"}  # Same instruction still pending: no new alert.
            prior.review_outcome = "NOT_APPLICABLE"
            prior.review_note = "Superseded by a later settled daily review before any fills."
            prior.save(update_fields=["review_outcome", "review_note"])
    if result.status != "action_needed":
        return {"status": "ok"}  # Avoid a daily HOLD notification.
    StrategySignal.objects.get_or_create(
        strategy=instance, signal_key="%s:%s:%s" % (KEY, instance.pk, as_of.isoformat()),
        defaults={"kind": "QUARTERLY", "as_of": as_of,
                  "title": "Nasdaq trend review %s" % as_of.isoformat(),
                  "message": result.reason,
                  "actions": [t.as_action() for t in result.trades],
                  "strategy_version": v1.VERSION})
    return {"status": "ok"}


def current_weights(instance):
    """The opening-buy and allocation cards use the same daily trend."""
    from tracker.models import MarketObservation
    funds = role_symbols(instance)
    recent = MarketObservation.objects.filter(symbol="SPY", currency="USD").order_by("-as_of").first()
    if not recent or recent.as_of < timezone.localdate() - timedelta(days=8):
        return None
    signal, _ = _signal(recent.as_of)
    if signal is None:
        return None
    return {symbol: Decimal("1") if signal.mode == role else Decimal("0")
            for role, symbol in funds.items()}


from tracker.strategies.nasdaq_trend_dca.study import TrendStudy
from tracker.study_fixed import Source

STUDY = {
    "GBP": TrendStudy(title="3x Nasdaq Trend + DCA", sources={
        "leveraged": Source("LQQ3.L", "3x Nasdaq-100 (LQQ3)", "TQQQ",
                            "TQQQ in USD (different ETP structure)"),
        "unleveraged": Source("EQQQ.L", "Nasdaq-100 (EQQQ)", "QQQ",
                              "QQQ in USD"),
    }),
    "USD": TrendStudy(title="3x Nasdaq Trend + DCA", currency="USD", sources={
        "leveraged": Source("TQQQ", "3x Nasdaq-100 (TQQQ)"),
        "unleveraged": Source("QQQ", "Nasdaq-100 (QQQ)"),
    }),
}

NASDAQ_TREND_DCA = StrategyModule(
    key=KEY, review=review, next_review=next_review,
    last_review_before=last_review_before,
    current_review_floor=current_review_floor,
    reference_symbols=("SPY", "QQQ"),
    cadence_word="daily", allocation_label="Trend allocation",
    current_weights=current_weights, study=STUDY,
    allows_cash_only=True,
)
