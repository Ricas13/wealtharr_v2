"""Owner-only Yahoo prices and conservative 9Sig quarterly review.

References: Jason Kelly's published Signal/30 Down explanations and the owner's
signal strategy simulation v1.07 workbook. Suggestions never place orders.
"""
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone as utc_timezone
from decimal import Decimal, ROUND_DOWN
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.db import transaction
from django.utils import timezone

from .models import (
    LedgerEntry, MarketObservation, PriceMark, StrategySignal, YahooSync,
)
from .services import ledger_summary, dispatch
from .notification_routing import route_notice
from .core.prices import check_price_series

LOG = logging.getLogger(__name__)
ZERO = Decimal("0")
CENTS = Decimal("0.01")
PRECISION = Decimal("0.0001")
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.^=_-]{0,23}$")


def fetch_chart(symbol, since):
    """Publicly accessible but unofficial Yahoo endpoint. No authentication bypass."""
    if not TICKER.fullmatch(symbol):
        raise ValueError("Invalid Yahoo symbol")
    params = urlencode({
        "period1": int(datetime.combine(since, datetime.min.time(), utc_timezone.utc).timestamp()),
        "period2": int(timezone.now().timestamp()) + 86400,
        "interval": "1d",
    })
    last_error = None
    chart = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        endpoint = "https://" + host + "/v8/finance/chart/" + quote(symbol, safe="") + "?" + params
        req = Request(endpoint, headers={
            "User-Agent": "Mozilla/5.0 (compatible; WealtharrOwnerTracker/0.2)",
            "Accept": "application/json",
        })
        try:
            with urlopen(req, timeout=14) as response:
                if response.status != 200:
                    raise ValueError("Yahoo chart returned a non-200 status")
                payload = json.loads(response.read(4_000_000))
            chart = (payload.get("chart", {}).get("result") or [None])[0]
            if chart:
                break
        except (OSError, ValueError) as exc:
            last_error = exc
        # Alternate official Yahoo chart host is a best-effort fallback, not
        # a market-data SLA or a licence to redistribute historical quotes.
    if chart is None and last_error is not None:
        raise last_error
    if not chart:
        raise ValueError("Yahoo supplied no chart data")
    raw_currency = chart.get("meta", {}).get("currency")
    # Exact pinned product/line unit checks. A Yahoo GBX/GBP metadata change
    # must not create a fictitious 100x portfolio valuation.
    expected_units = {"LQQ3.L": ("GBp", "GBX"),
                      "ERNS.L": ("GBP",),
                      "TQQQ": ("USD",), "SPY": ("USD",)}
    if symbol in expected_units and raw_currency not in expected_units[symbol]:
        raise ValueError("PRICE_HOLD:RAW_UNIT_MISMATCH")
    currency = "GBP" if raw_currency in ("GBp", "GBX", "GBP") else raw_currency
    if currency not in ("GBP", "USD"):
        raise ValueError("Unsupported Yahoo quote currency")
    divisor = Decimal(100) if raw_currency in ("GBp", "GBX") else Decimal(1)
    closes = ((chart.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    series = []
    for epoch, raw in zip(chart.get("timestamp") or [], closes):
        if raw is None:
            continue
        try:
            amount = (Decimal(str(raw)) / divisor).quantize(Decimal("0.00000001"))
            day = datetime.fromtimestamp(epoch, tz=utc_timezone.utc).date()
            if amount > ZERO and day <= timezone.localdate():
                series.append((day, amount))
        except (ValueError, ArithmeticError, OverflowError):
            continue
    series = sorted(dict(series).items())
    if not series:
        raise ValueError("No usable Yahoo daily closes")
    return currency, series


def sync_ticker(symbol, *, force=False):
    """One fetch per 12 hours per symbol, with durable prices and bounded errors."""
    if not TICKER.fullmatch(symbol):
        raise ValueError("Invalid Yahoo symbol")
    state, _ = YahooSync.objects.get_or_create(symbol=symbol)
    now = timezone.now()
    if (not force and state.last_attempt and state.last_attempt > now -
            (timedelta(minutes=30) if state.last_error else timedelta(hours=12))):
        return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()
    first = not MarketObservation.objects.filter(symbol=symbol).exists()
    since = date(2010, 1, 1) if first else timezone.localdate() - timedelta(days=18)
    state.last_attempt = now
    state.save(update_fields=["last_attempt"])
    try:
        currency, series = fetch_chart(symbol, since)
        # Screen the new batch before *any* upsert. A suspicious daily jump
        # (including an unhandled split/restrike or 100x pence error) stays
        # on hold; no later signal may use the older mark as though fresh.
        prior = MarketObservation.objects.filter(symbol=symbol, as_of__lt=series[0][0]).order_by("-as_of").first()
        checked = check_price_series(
            symbol, series, as_of=timezone.localdate(),
            previous=(prior.as_of, prior.price) if prior else None)
        if checked.status != "APPROVED":
            state.last_error = ("PRICE_HOLD:" + checked.code)[:80]
            state.save(update_fields=["last_error"])
            LOG.warning("Market price on hold for %s (%s)", symbol, checked.code)
            return None
        # A revised feed close may indicate a split, wrong units or source
        # restatement. Never silently overwrite accepted history.
        previous_values = dict(MarketObservation.objects.filter(
            symbol=symbol, as_of__in=[day for day, _ in series]
        ).values_list("as_of", "price"))
        if any(previous_values[day] != price for day, price in series
               if day in previous_values):
            state.last_error = "PRICE_HOLD:REVISED_HISTORICAL_CLOSE"
            state.save(update_fields=["last_error"])
            LOG.warning("Market price revision on hold for %s", symbol)
            return None
        observations = [
            MarketObservation(symbol=symbol, as_of=day, price=price, currency=currency)
            for day, price in series
        ]
        MarketObservation.objects.bulk_create(
            observations, batch_size=250, ignore_conflicts=True,
        )
        state.last_success = timezone.now()
        state.last_error = ""
        state.save(update_fields=["last_success", "last_error"])
    except Exception as exc:
        # Network errors are surfaced as a status, never replaced with invented data.
        detail = str(exc)
        state.last_error = (detail[:80] if detail.startswith("PRICE_HOLD:")
                            else type(exc).__name__[:80])
        state.save(update_fields=["last_error"])
        LOG.warning("Yahoo quote unavailable for %s (%s)", symbol, type(exc).__name__)
        return None
    return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()


# Pure, immutable versioned rule implementation (Django-free).
from .strategies.nine_sig.v2 import (
    VERSION as NINE_SIG_RULE_VERSION, quarter_number, prior_quarter_name,
    quarter_end_before, _quarterly_closes, _drawdown_from_spy,
    signal_line_value, advice_for_holdings, first_100_up_crossing,
)

def settlement_close(symbol, quarter_end, *, days=6):
    """Use last verified daily close on/before quarter end, never later data."""
    return MarketObservation.objects.filter(
        symbol=symbol, as_of__lte=quarter_end,
        as_of__gte=quarter_end - timedelta(days=days),
    ).order_by("-as_of").first()



def count_skipped_equity_sells(quarterly_signals):
    """A BUY shortfall HOLD is not a skipped SELL under 30 Down.

    Older versions also encoded cash shortages as HOLD. Counting every HOLD
    as a deferred sale could force an incorrect 60/40 reset on the next sale.
    """
    return sum(
        any(a.get("side") == "HOLD" for a in row.actions) and
        ("defer this equity sell" in row.message.lower() or
         "skip this sell" in row.message.lower())
        for row in quarterly_signals
    )


def _alert_intraperiod_spike(strategy, *, now, first_buy, baseline, tqqq_latest):
    """Possible mid-quarter 100%-up rule, using verified daily US closes.

    Exact Kelly spike reference is last executed quarterly rebalance price,
    not necessarily the calendar-quarter closing price. Until that is known,
    this is a warning only, never a made-up automatic GBP trading order.
    """
    if (tqqq_latest is None or tqqq_latest.currency != "USD" or
            quarter_number(tqqq_latest.as_of) != quarter_number(now) or
            tqqq_latest.as_of > now):
        return
    last_quarter = quarter_end_before(now)
    # Do not apply another investor's previous-quarter execution reference to
    # a portfolio which only entered 9Sig during the current quarter.
    if first_buy.occurred_on > last_quarter:
        return
    if baseline is not None and baseline.as_of > last_quarter:
        return
    origin = settlement_close("TQQQ", last_quarter)
    if not origin or origin.currency != "USD" or origin.price <= ZERO:
        return
    candidates = list(MarketObservation.objects.filter(
        symbol="TQQQ", currency="USD", as_of__gt=last_quarter,
        as_of__lte=tqqq_latest.as_of,
    ).order_by("as_of").values_list("as_of", "price"))
    crossing = first_100_up_crossing(
        origin_on=last_quarter, origin_usd=origin.price,
        daily_usd=candidates, until=tqqq_latest.as_of)
    crossed = (MarketObservation.objects.filter(
        symbol="TQQQ", currency="USD", as_of=crossing[0]).first()
        if crossing else None)
    if not crossed or first_buy.occurred_on > crossed.as_of:
        return
    if baseline is not None and baseline.as_of >= crossed.as_of:
        return
    start_date = baseline.as_of if baseline else first_buy.occurred_on
    down_notice = strategy.signals.filter(
        kind="THIRTY_DOWN",
        as_of__gte=max(start_date, last_quarter - timedelta(days=750)),
    ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("-as_of").first()
    if down_notice and quarter_number(now) - quarter_number(down_notice.as_of) < 8:
        return
    if strategy.ledger.filter(occurred_on__gt=crossed.as_of).exists():
        return
    q = quarter_number(now)
    key = "spike:%04d-Q%d" % (q // 4, q % 4 + 1)
    message = (
        "TQQQ USD daily close %s on %s reached at least twice the "
        "preceding calendar-quarter closing reference %s. "
        "Possible 100%%-up event: review a reset to 60%% equity / 40%% "
        "safe assets. Jason Kelly's reference may be the actual prior "
        "quarterly rebalance execution price, which can differ from "
        "the calendar quarter close. Check that reference, your prior "
        "allocation and 30 Down status before trading. No GBP orders "
        "or quantities have been invented."
    ) % (crossed.price, crossed.as_of, origin.price)
    signal, created = StrategySignal.objects.get_or_create(
        strategy=strategy, signal_key=key,
        defaults={"kind": "SPIKE_WATCH",
                  "title": "9Sig 100%-up spike: manual reset review",
                  "message": message, "as_of": crossed.as_of, "actions": [],
                  "strategy_version": NINE_SIG_RULE_VERSION})
    if created:
        route_notice(strategy.owner, "urgent",
                     "signal:%s:%s" % (strategy.pk, key),
                     "Wealtharr — " + signal.title,
                     strategy.name + "\n" + message + "\nNo orders placed.",
                     sender=dispatch)


def review_strategy(strategy):
    """Use actual *completed-quarter* prices and ledger holdings, not today's value."""
    if not strategy.market_data_enabled or not strategy.preset.active:
        return {"status": "disabled"}
    if strategy.preset.slug != "nasdaq-quarterly-target":
        return {"status": "unsupported"}
    # Never run v2 against an old pinned v1 owner portfolio implicitly.
    if strategy.strategy_version and strategy.strategy_version != NINE_SIG_RULE_VERSION:
        return {"status": "cannot_compute", "reason": "Pinned 9Sig rule version differs; owner review required"}
    if (not TICKER.fullmatch(strategy.equity_symbol) or
            not TICKER.fullmatch(strategy.safe_symbol)):
        return {"status": "invalid symbol"}
    if strategy.account.currency != "GBP":
        return {"status": "unsupported account currency"}
    report = ledger_summary(strategy)
    if not report["positions"]:
        return {"status": "waiting for first purchase"}
    if any(symbol not in (strategy.equity_symbol, strategy.safe_symbol)
           for symbol, qty in report["positions"].items() if qty > ZERO):
        return {"status": "holdings outside the two configured strategy sleeves"}
    tickers = {strategy.equity_symbol, strategy.safe_symbol, "TQQQ", "SPY"}
    data = {symbol: sync_ticker(symbol) for symbol in tickers}
    # Fail closed even if old accepted observations remain in the database.
    # The health issue must be reviewed rather than silently ignored.
    if YahooSync.objects.filter(symbol__in=tickers,
            last_error__startswith="PRICE_HOLD:").exists():
        return {"status": "cannot_compute", "reason": "Market price on hold for review"}
    now = timezone.localdate()
    # Current price marks are for the portfolio dashboard, never for a past
    # quarter's signal. A failed Yahoo lookup cannot invent a new valuation.
    for symbol in (strategy.equity_symbol, strategy.safe_symbol):
        close = data.get(symbol)
        if close and close.currency == "GBP" and close.as_of >= now - timedelta(days=8):
            previous = PriceMark.objects.filter(strategy=strategy, symbol=symbol).first()
            if not previous or previous.as_of <= close.as_of:
                PriceMark.objects.update_or_create(
                    strategy=strategy, symbol=symbol,
                    defaults={"price": close.price, "as_of": close.as_of, "source": "yahoo"})
    first_buy = strategy.ledger.filter(
        kind=LedgerEntry.BUY, symbol=strategy.equity_symbol,
    ).order_by("occurred_on", "pk").first()
    if not first_buy:
        return {"status": "waiting for equity purchase"}
    quarter_end = quarter_end_before(now)
    baseline = strategy.baselines.order_by("-as_of", "-pk").first()
    start_date = baseline.as_of if baseline else first_buy.occurred_on
    if quarter_number(quarter_end) < quarter_number(start_date):
        return {"status": "waiting for first completed quarter"}
    # A 60/40 reset is a suggestion, never proof that the owner traded.
    # Future cycles are paused until their actual positions are re-anchored.
    pending_reset = strategy.signals.filter(
        kind__in=("QUARTERLY", "SPIKE_WATCH"), as_of__lt=quarter_end,
        message__icontains="review a reset to 60%"
    ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("-as_of").first()
    if pending_reset and (baseline is None or baseline.as_of <= pending_reset.as_of):
        return {"status": "manual reset must be confirmed as a new strategy baseline"}
    _alert_intraperiod_spike(
        strategy, now=now, first_buy=first_buy, baseline=baseline,
        tqqq_latest=data.get("TQQQ"))
    closing = {symbol: settlement_close(symbol, quarter_end) for symbol in tickers}
    if (any(x is None for x in closing.values()) or
            closing[strategy.equity_symbol].currency != "GBP" or
            closing[strategy.safe_symbol].currency != "GBP" or
            closing["TQQQ"].currency != "USD" or closing["SPY"].currency != "USD"):
        return {"status": "incomplete or currency-mismatched quarter-end prices"}
    # Replay only actual fills dated by the quarterly settlement.
    cash = ZERO
    positions = {}
    injected = []
    for entry in strategy.ledger.filter(occurred_on__lte=quarter_end).order_by("occurred_on", "pk"):
        qty = positions.get(entry.symbol, ZERO)
        if entry.kind == LedgerEntry.DEPOSIT:
            cash += entry.amount
            if entry.occurred_on > start_date:
                injected.append((entry.occurred_on, entry.amount))
        elif entry.kind == LedgerEntry.WITHDRAW:
            cash -= entry.amount
            if entry.occurred_on > start_date:
                injected.append((entry.occurred_on, -entry.amount))
        elif entry.kind == LedgerEntry.DIVIDEND:
            cash += entry.amount
        elif entry.kind == LedgerEntry.FEE:
            cash -= entry.amount
        elif entry.kind == LedgerEntry.BUY:
            cash -= (entry.quantity * entry.unit_price).quantize(CENTS) + entry.fee
            positions[entry.symbol] = qty + entry.quantity
        elif entry.kind == LedgerEntry.SELL:
            cash += (entry.quantity * entry.unit_price).quantize(CENTS) - entry.fee
            positions[entry.symbol] = qty - entry.quantity
    stock_qty = positions.get(strategy.equity_symbol, ZERO)
    safe_qty = positions.get(strategy.safe_symbol, ZERO)
    if stock_qty <= ZERO or safe_qty < ZERO or cash < ZERO:
        return {"status": "historical holdings cannot support an advisory review"}
    # Full observed TQQQ history is informational for the running-high
    # drawdown; the trade rules still use only completed-quarter references.
    ref_tqqq = list(MarketObservation.objects.filter(
        symbol="TQQQ", as_of__lte=quarter_end,
    ).order_by("as_of").values_list("as_of", "price"))
    ref_spy = list(MarketObservation.objects.filter(
        symbol="SPY", as_of__lte=quarter_end,
        as_of__gte=quarter_end - timedelta(days=760),
    ).order_by("as_of").values_list("as_of", "price"))
    # The 30 Down clock starts with a detected 30 Down notice, not
    # with a period of no sales. Previous unrelated HOLDs do not count.
    active_down_signal = strategy.signals.filter(
        kind="THIRTY_DOWN", as_of__lt=quarter_end,
        as_of__gte=start_date,
    ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("-as_of", "-pk").first()
    down_age = (quarter_number(quarter_end) - quarter_number(active_down_signal.as_of)
                if active_down_signal else 0)
    skipped = 0
    if active_down_signal and down_age <= 8:
        earlier = strategy.signals.filter(
            kind="QUARTERLY", as_of__lt=quarter_end,
            as_of__gte=active_down_signal.as_of,
        ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("as_of")
        skipped = count_skipped_equity_sells(earlier)
    if len(_quarterly_closes(ref_spy, quarter_end)) < 2:
        return {"status": "insufficient SPY quarter-end reference history"}
    if len(_quarterly_closes(ref_tqqq, quarter_end)) < 2:
        return {"status": "insufficient TQQQ quarter-end reference history"}
    # Initial equity baseline includes the first purchase date only.
    # Subsequent buys in the first calendar quarter must not silently
    # inflate the baseline and double-count their funding contributions.
    initial_equity_cost = baseline.equity_value if baseline else sum(
        (t.quantity * t.unit_price for t in strategy.ledger.filter(
            symbol=strategy.equity_symbol, kind=LedgerEntry.BUY,
            occurred_on=first_buy.occurred_on)),
        ZERO)
    if initial_equity_cost <= ZERO:
        return {"status": "missing initial equity investment"}
    alerts = advice_for_holdings(
        start_date=start_date, as_of=quarter_end,
        currency=strategy.account.currency,
        equity_symbol=strategy.equity_symbol,
        safe_symbol=strategy.safe_symbol,
        equity_qty=stock_qty, equity_price=closing[strategy.equity_symbol].price,
        safe_qty=safe_qty, safe_price=closing[strategy.safe_symbol].price,
        cash=cash, starting_equity_cost=initial_equity_cost,
        contributions=injected, reference=ref_tqqq, spy_reference=ref_spy,
        prior_skipped_sells=skipped, prior_drawdown=bool(active_down_signal),
        down_age_quarters=down_age,
    )
    created = 0
    for alert in alerts:
        signal, new = StrategySignal.objects.get_or_create(
            strategy=strategy, signal_key=alert["key"],
            defaults={"kind": alert["kind"], "title": alert["title"],
                      "message": alert["message"], "actions": alert["actions"],
                      "reset_reason": alert.get("reset_reason", ""),
                      "strategy_version": NINE_SIG_RULE_VERSION,
                      "as_of": quarter_end})
        if new:
            created += 1
            details = "\n".join(
                "%s %s shares %s (estimated £%s)" %
                (a["side"], a["quantity"], a["symbol"], a["estimated_gbp"])
                for a in alert["actions"])
            # No-action quarterly reviews remain in history without
            # triggering rebalance alerts or cluttering the Overview.
            actionable = alert["kind"] == "THIRTY_DOWN" or any(
                action.get("side") in ("BUY", "SELL") for action in alert["actions"])
            if actionable and alert["kind"] == "THIRTY_DOWN":
                route_notice(strategy.owner, "urgent",
                             "signal:%s:%s" % (strategy.pk, signal.signal_key),
                             "Wealtharr — " + signal.title,
                             strategy.name + "\n" + signal.message + "\n" + details +
                             "\nReview this in Wealtharr. No orders have been placed.",
                             sender=dispatch)
            elif not actionable:
                signal.acknowledged_at = timezone.now()
                signal.save(update_fields=["acknowledged_at"])
    return {"status": "ok", "created": created, "as_of": quarter_end.isoformat()}
