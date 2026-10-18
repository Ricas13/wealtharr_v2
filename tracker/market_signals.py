"""Server-only Yahoo chart sync and conservative advisory 9Sig review.

No auto-trading. This is a disclosed subset of the uploaded spreadsheet methodology;
cash-shortage, skip-sale and peak-reset exception branches require parity work.
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
        observations = [
            MarketObservation(symbol=symbol, as_of=day, price=price, currency=currency)
            for day, price in series
        ]
        MarketObservation.objects.bulk_create(
            observations, batch_size=250, update_conflicts=True,
            update_fields=["price", "currency"], unique_fields=["symbol", "as_of"],
        )
        state.last_success = timezone.now()
        state.last_error = ""
        state.save(update_fields=["last_success", "last_error"])
    except Exception as exc:
        # Network errors are surfaced as a status, never replaced with invented data.
        state.last_error = type(exc).__name__[:80]
        state.save(update_fields=["last_error"])
        LOG.warning("Yahoo quote unavailable for %s (%s)", symbol, type(exc).__name__)
        return None
    return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()


def quarter_number(day):
    return day.year * 4 + (day.month - 1) // 3


def prior_quarter_name(day):
    index = quarter_number(day) - 1
    return "%04d-Q%d" % (index // 4, index % 4 + 1)


def quarter_end_before(day):
    """Last completed calendar quarter; no mid-quarter valuation used for a signal."""
    if day.month <= 3:
        return date(day.year - 1, 12, 31)
    if day.month <= 6:
        return date(day.year, 3, 31)
    if day.month <= 9:
        return date(day.year, 6, 30)
    return date(day.year, 9, 30)


def settlement_close(symbol, quarter_end, *, days=6):
    """Use last verified daily close on/before quarter end, never later data."""
    return MarketObservation.objects.filter(
        symbol=symbol, as_of__lte=quarter_end,
        as_of__gte=quarter_end - timedelta(days=days),
    ).order_by("-as_of").first()


def _quarterly_closes(reference, until):
    closes = {}
    for day, price in sorted(reference):
        if day <= until and price > ZERO:
            closes[quarter_number(day)] = (day, price)
    return closes


def _amount(quantity, price):
    return str((quantity * price).quantize(CENTS))


def _order(side, symbol, qty, price):
    return {"side": side, "symbol": symbol, "quantity": str(qty),
            "estimated_gbp": _amount(qty, price)}


def _floor_qty(amount, price):
    if amount <= ZERO or price <= ZERO:
        return ZERO
    return (amount / price).quantize(PRECISION, rounding=ROUND_DOWN)


def signal_line_value(*, start_date, as_of, starting_equity_cost, contributions=()):
    """Reproduce the workbook AV recurrence without advancing deposits early.

    Each completed quarter grows the line 9%; half of contributions deposited
    *during* that quarter enter only after that growth, per
    AV4 = AV3 * (1 + C9) + BV4/2. Earlier deposits grow in later quarters.
    """
    from calendar import monthrange
    at_close = as_of.month % 3 == 0 and as_of.day == monthrange(as_of.year, as_of.month)[1]
    review_quarter = quarter_number(as_of) if at_close else quarter_number(as_of) - 1
    first_quarter = quarter_number(start_date)
    if review_quarter < first_quarter:
        return None
    target = Decimal(starting_equity_cost) * Decimal("1.09") ** (review_quarter - first_quarter + 1)
    for day, amount in contributions:
        day_q = quarter_number(day)
        if start_date < day <= as_of and first_quarter <= day_q <= review_quarter:
            target += (Decimal(amount) / 2) * Decimal("1.09") ** (review_quarter - day_q)
    return target.quantize(CENTS)


def _drawdown_from_spy(spy, review_q):
    """30 Down requires falling SPY vs the highest of up to 8 quarter closes."""
    recent = [spy[q][1] for q in sorted(spy)
              if review_q - 7 <= q <= review_q]
    if len(recent) < 2:
        return False, ZERO
    previous = spy.get(review_q - 1)
    current = spy.get(review_q)
    if not current or not previous:
        return False, ZERO
    peak = max(recent)
    decline = (Decimal(1) - current[1] / peak) if peak else ZERO
    return (decline >= Decimal("0.30") and current[1] < previous[1]), decline


def advice_for_holdings(*, start_date, as_of, currency, equity_symbol, safe_symbol,
                        equity_qty, equity_price, safe_qty, safe_price, cash,
                        starting_equity_cost, reference,
                        spy_reference=None, contributions=(), prior_skipped_sells=0,
                        no_sell_quarters=0, prior_drawdown=False,
                        down_age_quarters=0):
    """Deterministic owner-review 9Sig action on a completed quarter.

    60/40 baseline, 9% quarterly signal growth, half of period contributions,
    maximum 90% of safe assets used for regular buys, 30 Down two-sell skip,
    eight-quarter expiry, 100%-up single-quarter spike, and base reset on
    >100% safe-sleeve requirement. The owner executes and records all fills.
    """
    if (currency != "GBP" or equity_qty <= ZERO or starting_equity_cost <= ZERO or
            equity_price <= ZERO or safe_price <= ZERO or safe_qty < ZERO or cash < ZERO):
        return []
    from calendar import monthrange
    at_close = as_of.month % 3 == 0 and as_of.day == monthrange(as_of.year, as_of.month)[1]
    review_q = quarter_number(as_of) if at_close else quarter_number(as_of) - 1
    target = signal_line_value(
        start_date=start_date, as_of=as_of,
        starting_equity_cost=starting_equity_cost, contributions=contributions)
    if target is None:
        return []

    equity_value = equity_qty * equity_price
    safe_value = safe_qty * safe_price
    total = equity_value + safe_value + cash
    if total <= ZERO:
        return []
    difference = target - equity_value
    tolerance = max(Decimal("1"), total * Decimal("0.005"))

    spy = _quarterly_closes(spy_reference or (), as_of)
    down_trigger, decline = _drawdown_from_spy(spy, review_q)
    # Once triggered, the skip window persists until 2 ignored sells or
    # eight quarters. Recovery in a later quarter does not silently erase
    # the two skipped-sell state.
    active_down = down_trigger or (prior_drawdown and down_age_quarters < 8)
    alerts = []
    if down_trigger and not prior_drawdown:
        alerts.append({
            "kind": "THIRTY_DOWN",
            "key": "30down:" + str(review_q),
            "title": "9Sig 30 Down: next two sell signals are deferred",
            "message": (
                "SPY fell %.2f%% from its rolling eight-quarter closing peak. "
                "The next two equity sell signals are skipped; buy signals "
                "continue, with the third would-be sell requiring a 60/40 reset. "
                "The window expires after eight quarters. No trade was placed."
            ) % float(decline * 100),
            "actions": [],
        })

    tqqq = _quarterly_closes(reference, as_of)
    current = tqqq.get(review_q)
    previous = tqqq.get(review_q - 1)
    spike = (previous is not None and current is not None and previous[1] > ZERO
             and current[1] >= 2 * previous[1])
    reset_reason = None
    note = ""
    if prior_drawdown and down_age_quarters >= 8:
        reset_reason = "Eight-quarter 30 Down safety window expired"
        active_down = False
    elif active_down and difference < -tolerance:
        if prior_skipped_sells >= 2:
            reset_reason = "Two equity sells were already skipped under 30 Down"
        else:
            note = "30 Down active: defer this equity sell signal; do not rebalance."
    elif spike and not active_down:
        reset_reason = "TQQQ reference gained at least 100% in the completed quarter"
    elif difference > cash + safe_value + tolerance and not active_down:
        reset_reason = "The equity buy would require selling more than 100% of safe assets"
    elif active_down and difference > cash + safe_value + tolerance:
        note = "30 Down active: safe-asset shortage. Review before committing money."

    if not note:
        note = ("Equity below quarterly signal line."
                if difference > tolerance else
                "Equity above quarterly signal line."
                if difference < -tolerance else
                "Equity matches the quarterly signal line; no rebalance needed.")

    if reset_reason:
        note = reset_reason + ": review a reset to 60% equity / 40% safe and cash."
        difference = total * Decimal("0.60") - equity_value

    actions = []
    if active_down and difference < -tolerance and not reset_reason:
        actions.append({"side": "HOLD", "symbol": equity_symbol,
                        "quantity": "0", "estimated_gbp": "0.00"})
    elif difference < -tolerance:
        equity_sell = min(equity_qty, _floor_qty(-difference, equity_price))
        if equity_sell > ZERO:
            actions.append(_order("SELL", equity_symbol, equity_sell, equity_price))
        # All proceeds plus existing cash can fund safe purchases. The cash
        # remainder from rounding stays in the strategy account.
        safe_budget = cash + equity_sell * equity_price
        safe_buy = _floor_qty(safe_budget, safe_price)
        if safe_buy > ZERO:
            actions.append(_order("BUY", safe_symbol, safe_buy, safe_price))
    elif difference > tolerance:
        if reset_reason:
            # Explicit reset can use the safe sleeve down to a 40% balance;
            # the regular 90% throttle does not apply to a forced reset.
            desired_safe_sale = max(ZERO, difference - cash)
            safe_sale_budget = min(safe_value, desired_safe_sale)
        else:
            safe_sale_budget = min(
                safe_value * Decimal("0.90"), max(ZERO, difference - cash))
        safe_to_sell = min(safe_qty, _floor_qty(safe_sale_budget, safe_price))
        if safe_to_sell > ZERO:
            actions.append(_order("SELL", safe_symbol, safe_to_sell, safe_price))
        available = cash + safe_to_sell * safe_price
        equity_buy = _floor_qty(min(difference, available), equity_price)
        if equity_buy > ZERO:
            actions.append(_order("BUY", equity_symbol, equity_buy, equity_price))
        if available + tolerance < difference:
            note += " Available GBP buying power limits the order size."

    label = "%04d-Q%d" % (review_q // 4, review_q % 4 + 1)
    alerts.append({
        "kind": "QUARTERLY", "key": "quarter:" + label,
        "title": "9Sig quarterly review — " + label,
        "message": (
            "%s Signal line £%s = a 9%% rise each completed quarter "
            "plus half of deposits in that quarter. GBP prices and proposed "
            "unit quantities are estimates based on closing quotes. "
            "Review before trading; no orders have been placed."
        ) % (note, target),
        "actions": actions,
    })
    return alerts


def review_strategy(strategy):
    """Use actual *completed-quarter* prices and ledger holdings, not today's value."""
    if not strategy.market_data_enabled or not strategy.preset.active:
        return {"status": "disabled"}
    if strategy.preset.slug != "nasdaq-quarterly-target":
        return {"status": "unsupported"}
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
        kind="QUARTERLY", as_of__lt=quarter_end,
        message__icontains="review a reset to 60%"
    ).order_by("-as_of").first()
    if pending_reset and (baseline is None or baseline.as_of <= pending_reset.as_of):
        return {"status": "manual reset must be confirmed as a new strategy baseline"}
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
    ref_tqqq = list(MarketObservation.objects.filter(
        symbol="TQQQ", as_of__lte=quarter_end,
        as_of__gte=start_date - timedelta(days=100),
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
    ).order_by("-as_of", "-pk").first()
    down_age = (quarter_number(quarter_end) - quarter_number(active_down_signal.as_of)
                if active_down_signal else 0)
    skipped = 0
    if active_down_signal and down_age <= 8:
        earlier = strategy.signals.filter(
            kind="QUARTERLY", as_of__lt=quarter_end,
            as_of__gte=active_down_signal.as_of,
        ).order_by("as_of")
        skipped = sum(
            any(action.get("side") == "HOLD" for action in row.actions)
            for row in earlier
        )
    if len(_quarterly_closes(ref_spy, quarter_end)) < 2:
        return {"status": "insufficient SPY quarter-end reference history"}
    if len(_quarterly_closes(ref_tqqq, quarter_end)) < 2:
        return {"status": "insufficient TQQQ quarter-end reference history"}
    # Initial holdings may be split into several orders in the first quarter.
    from calendar import monthrange
    first_quarter_last_month = ((first_buy.occurred_on.month - 1) // 3 + 1) * 3
    initial_end = date(first_buy.occurred_on.year, first_quarter_last_month,
                       monthrange(first_buy.occurred_on.year, first_quarter_last_month)[1])
    initial_equity_cost = baseline.equity_value if baseline else sum(
        (t.quantity * t.unit_price for t in strategy.ledger.filter(
            symbol=strategy.equity_symbol, kind=LedgerEntry.BUY,
            occurred_on__gte=first_buy.occurred_on, occurred_on__lte=initial_end)),
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
            if actionable:
                dispatch(strategy.owner, "signal:%s:%s" % (strategy.pk, signal.signal_key),
                         "Wealtharr — " + signal.title,
                         strategy.name + "\n" + signal.message + "\n" + details +
                         "\nReview this in Wealtharr. No orders have been placed.")
            else:
                signal.acknowledged_at = timezone.now()
                signal.save(update_fields=["acknowledged_at"])
    return {"status": "ok", "created": created, "as_of": quarter_end.isoformat()}
