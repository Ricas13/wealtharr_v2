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


def last_review_before(day):
    """Latest *observed* completed US session, including holidays/weekends."""
    from tracker.models import MarketObservation
    return (MarketObservation.objects.filter(symbol="SPY", currency="USD",
            as_of__lte=day).order_by("-as_of")
            .values_list("as_of", flat=True).first() or day - timedelta(days=1))


def next_review(day):
    """Informational next weekday; actual reviews require settled US closes."""
    at = day + timedelta(days=1)
    while at.weekday() > 4:
        at += timedelta(days=1)
    return at


def _reference(as_of):
    from tracker.models import MarketObservation, YahooSync
    if YahooSync.objects.filter(symbol__in=("SPY", "QQQ"),
                                last_error__startswith="PRICE_HOLD:").exists():
        return None
    series = {}
    for symbol in ("SPY", "QQQ"):
        series[symbol] = list(MarketObservation.objects.filter(
            symbol=symbol, currency="USD", as_of__lte=as_of,
            price__gt=0).order_by("as_of").values_list("as_of", "price"))
    return v1.trend_at(series["SPY"], series["QQQ"], as_of)


def _completed_dca(instance, signal):
    """Read only actually finished reviews; proposals aren't fictional fills."""
    from tracker.models import RebalanceExecution
    if signal.regime != "off" or not signal.exit_on:
        return 0, False
    prefix = ":dca:%s:" % signal.exit_on.isoformat()
    completed = RebalanceExecution.objects.filter(
        strategy=instance, signal__signal_key__contains=prefix,
    ).select_related("signal")
    months = {e.signal.as_of.strftime("%Y-%m") for e in completed}
    return min(12, len(months)), signal.as_of.strftime("%Y-%m") in months


def decide(instance, as_of, *, signal=None, positions=None, cash=None):
    from tracker.market_data import settlement_close
    from tracker.models import YahooSync
    from tracker.services import replay
    from tracker.v2_ledger_core import effective_entries

    funds = role_symbols(instance)
    signal = signal or _reference(as_of)
    if signal is not None and signal.as_of != as_of:
        return v1.Result(v1.VERSION, "cannot_compute", "REFERENCE_GAP",
                         "SPY and QQQ do not have a matching completed close.")
    if positions is None:
        entries = [e for e in effective_entries(instance) if e.occurred_on <= as_of]
        cash, _, positions = replay(entries)
    if YahooSync.objects.filter(symbol__in=tuple(funds.values()),
                                last_error__startswith="PRICE_HOLD:").exists():
        return v1.Result(v1.VERSION, "cannot_compute", "PRICE_HOLD",
                         "A mapped instrument's price is on hold.")
    quotes = {}
    for symbol in funds.values():
        close = settlement_close(symbol, as_of, days=5)
        if close and close.currency == instance.account.currency and close.price > 0:
            quotes[symbol] = v1.Quote(symbol, Decimal(close.price), close.as_of)
    completed, done_this_month = _completed_dca(instance, signal) if signal else (0, False)
    if signal and signal.mode == "dca" and done_this_month:
        completed = max(completed, signal.months_elapsed)
    return v1.evaluate(funds=funds, positions=positions, cash=cash, quotes=quotes,
                       signal=signal, completed_dca=completed)


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
    signal = _reference(as_of)
    if signal is None or signal.as_of != as_of or signal.as_of < today - timedelta(days=8):
        return {"status": "cannot_compute", "reason": "No fresh settled 200-session signal."}
    result = decide(instance, as_of, signal=signal)
    if result.status == "cannot_compute":
        return {"status": "cannot_compute", "reason": result.reason}
    # A partly confirmed earlier review must be finished/corrected first.
    prior = StrategySignal.objects.filter(
        strategy=instance, kind="QUARTERLY",
    ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("-as_of", "-pk").first()
    if prior and prior.actions and not RebalanceExecution.objects.filter(signal=prior).exists():
        if SignalFill.objects.filter(signal=prior).exists():
            return {"status": "cannot_compute",
                    "reason": "Finish the earlier partial manual review before a new one."}
        # No fills: the old recommendation used older market data. Keep audit
        # history, but make the current close the only actionable proposal.
        if prior.as_of < as_of:
            prior.review_outcome = "NOT_APPLICABLE"
            prior.review_note = "Superseded by the next settled daily review before any fills."
            prior.save(update_fields=["review_outcome", "review_note"])
    if result.status != "action_needed":
        return {"status": "ok"}  # Avoid a daily HOLD notification.
    key = "%s:%s:%s" % (KEY, instance.pk, as_of.isoformat())
    if signal.mode == "dca":
        key += ":dca:%s:%s" % (signal.exit_on, as_of.strftime("%Y-%m"))
    StrategySignal.objects.get_or_create(
        strategy=instance, signal_key=key,
        defaults={"kind": "QUARTERLY", "as_of":as_of,
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
    signal = _reference(recent.as_of)
    if signal is None or signal.as_of != recent.as_of:
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
    reference_symbols=("SPY", "QQQ"),
    cadence_word="daily", allocation_label="Trend allocation",
    current_weights=current_weights, study=STUDY,
)
