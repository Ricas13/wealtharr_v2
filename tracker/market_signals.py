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
    endpoint = "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe="") + "?" + params
    req = Request(endpoint, headers={"User-Agent": "WealtharrOwnerTracker/0.2", "Accept": "application/json"})
    with urlopen(req, timeout=14) as response:
        if response.status != 200:
            raise ValueError("Yahoo chart rejected the request")
        obj = json.loads(response.read(4_000_000))
    chart = (obj.get("chart", {}).get("result") or [None])[0]
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
    if not force and state.last_attempt and state.last_attempt > now - timedelta(hours=12):
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


def advice_for_holdings(*, start_date, as_of, currency, equity_symbol, safe_symbol,
                        equity_qty, equity_price, safe_qty, safe_price, cash,
                        starting_equity_cost, reference,
                        spy_reference=None, contributions=(), prior_skipped_sells=0,
                        no_sell_quarters=0, prior_drawdown=False):
    """Deterministic, advisory 9Sig-style quarterly decision.

    Workbook AV4: previous signal line * (1 + C9), with C9=0.09 per QUARTER.
    Workbook BV4/2: add half of new contributions to the signal line.
    The other workbook instructions use owner-entered YES/NO flags. When a
    branch cannot be established we do NOT invent a trade.
    """
    if (currency != "GBP" or equity_qty <= ZERO or starting_equity_cost <= ZERO or
            equity_price <= ZERO or safe_price <= ZERO or safe_qty < ZERO or cash < ZERO):
        return []
    # A quarter's 9% increase applies at its close. For callers passing
    # the first day of the next quarter, use the last completed quarter.
    from calendar import monthrange
    at_close = as_of.month % 3 == 0 and as_of.day == monthrange(as_of.year, as_of.month)[1]
    steps = quarter_number(as_of) - quarter_number(start_date) + (1 if at_close else 0)
    if steps <= 0:
        return []
    target = starting_equity_cost * (Decimal("1.09") ** steps)
    # Each later period's net cash injections add 50% to the signal line, then
    # participate in subsequent quarterly 9% compounding.
    for day, contribution in contributions:
        offset = quarter_number(day) - quarter_number(start_date)
        if 0 < offset <= steps:
            target += (Decimal(contribution) / 2) * (Decimal("1.09") ** (steps - offset))
    target = target.quantize(CENTS)
    equity_value = equity_qty * equity_price
    safe_value = safe_qty * safe_price
    total = equity_value + safe_value + cash
    if total <= ZERO:
        return []
    # 30 Down does NOT sell half of equity. It suspends SELL signals.
    # SPY is the published reference; TQQQ is inspected for 100%-up resets.
    spy = _quarterly_closes(spy_reference or (), as_of)
    recent = [spy[q] for q in sorted(spy) if
              quarter_number(as_of) - 7 <= q <= quarter_number(as_of)]
    down = False
    decline = ZERO
    if len(recent) >= 2:
        latest_day, latest = recent[-1]
        peak = max(x[1] for x in recent)
        decline = (Decimal(1) - latest / peak)
        down = decline >= Decimal("0.30") and latest < recent[-2][1]
    active_down = down or (prior_drawdown and bool(recent) and recent[-1][1] < max(x[1] for x in recent) * Decimal("0.70"))
    alerts = []
    if down and not prior_drawdown:
        alerts.append({
            "kind": "THIRTY_DOWN",
            "key": "30down:" + str(quarter_number(as_of)),
            "title": "30 Down: suspend the next sell signals",
            "message": (
                "SPY has fallen %.2f%% from its last-eight-quarter closing peak. "
                "The 30 Down rule postpones up to two sell signals; it does not "
                "instruct an immediate half-position sale. Review the workbook "
                "flags before trading."
            ) % float(decline * 100),
            "actions": [],
        })
    raw_difference = target - equity_value
    tolerance = max(Decimal("1"), total * Decimal("0.005"))
    reset_reason = None
    if no_sell_quarters >= 8 and not active_down:
        reset_reason = "Eight consecutive quarters without a confirmed equity sale"
    tqqq = _quarterly_closes(reference, as_of)
    current = tqqq.get(quarter_number(as_of))
    previous = tqqq.get(quarter_number(as_of) - 1)
    if (not active_down and previous and current and
            previous[1] > ZERO and current[1] >= 2 * previous[1]):
        reset_reason = "The TQQQ reference doubled in a quarter"
    actions = []
    if active_down and raw_difference < -tolerance:
        if prior_skipped_sells >= 2:
            reset_reason = "Two sell signals have already been skipped during 30 Down"
        else:
            note = ("30 Down active: skip this sell signal. No shares should be "
                    "sold solely to follow the normal target this quarter.")
            actions = [{"side": "HOLD", "symbol": equity_symbol,
                        "quantity": "0", "estimated_gbp": "0.00"}]
    if reset_reason:
        goal = total * Decimal("0.60")
        raw_difference = goal - equity_value
        note = reset_reason + ": review a reset to 60% equity / 40% safe and cash."
    elif not (active_down and raw_difference < -tolerance):
        note = ("Equity below the 9%-per-quarter signal line."
                if raw_difference > tolerance else
                "Equity above the 9%-per-quarter signal line."
                if raw_difference < -tolerance else
                "Equity close to the signal line; no change suggested.")
    if not (active_down and raw_difference < -tolerance and not reset_reason):
        if raw_difference < -tolerance:
            sell_qty = min(equity_qty, _floor_qty(-raw_difference, equity_price))
            if sell_qty > ZERO:
                actions.append(_order("SELL", equity_symbol, sell_qty, equity_price))
                safe_buy = _floor_qty(cash + sell_qty * equity_price, safe_price)
                if safe_buy > ZERO:
                    actions.append(_order("BUY", safe_symbol, safe_buy, safe_price))
        elif raw_difference > tolerance:
            available_safe = safe_value * Decimal("0.90")
            requested = min(raw_difference, cash + available_safe)
            safe_sell_needed = max(ZERO, requested - cash)
            if safe_sell_needed > ZERO:
                safe_qty_to_sell = min(safe_qty, _floor_qty(safe_sell_needed, safe_price))
                if safe_qty_to_sell > ZERO:
                    actions.append(_order("SELL", safe_symbol, safe_qty_to_sell, safe_price))
            budget = cash + sum(Decimal(x["estimated_gbp"]) for x in actions if x["side"] == "SELL")
            buy_qty = _floor_qty(min(raw_difference, budget), equity_price)
            if buy_qty > ZERO:
                actions.append(_order("BUY", equity_symbol, buy_qty, equity_price))
            if budget < raw_difference:
                note += " Buying power is limited; record a cash shortage for manual review."
    signal_quarter = quarter_number(as_of) if at_close else quarter_number(as_of) - 1
    label = "%04d-Q%d" % (signal_quarter // 4, signal_quarter % 4 + 1)
    alerts.append({
        "kind": "QUARTERLY", "key": "quarter:" + label,
        "title": "9Sig quarterly review — " + label,
        "message": (
            "%s Signal line £%s = 9%% compounded each QUARTER from the "
            "initial equity sleeve, plus half of later contributions. "
            "Actual fills, trading costs, corporate actions and workbook "
            "exception flags must be confirmed manually. No orders placed."
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
    if quarter_number(quarter_end) < quarter_number(first_buy.occurred_on):
        return {"status": "waiting for first completed quarter"}
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
            if entry.occurred_on > first_buy.occurred_on:
                injected.append((entry.occurred_on, entry.amount))
        elif entry.kind == LedgerEntry.WITHDRAW:
            cash -= entry.amount
            if entry.occurred_on > first_buy.occurred_on:
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
        as_of__gte=first_buy.occurred_on - timedelta(days=100),
    ).order_by("as_of").values_list("as_of", "price"))
    ref_spy = list(MarketObservation.objects.filter(
        symbol="SPY", as_of__lte=quarter_end,
        as_of__gte=quarter_end - timedelta(days=760),
    ).order_by("as_of").values_list("as_of", "price"))
    old = list(strategy.signals.filter(
        kind="QUARTERLY", as_of__lt=quarter_end).order_by("as_of"))
    prior_down = strategy.signals.filter(
        kind="THIRTY_DOWN", as_of__lt=quarter_end,
        as_of__gte=quarter_end - timedelta(days=760)).exists()
    skipped = sum(
        any(a.get("side") == "HOLD" for a in row.actions)
        for row in old[-8:])
    last_sell = strategy.ledger.filter(
        kind=LedgerEntry.SELL, symbol=strategy.equity_symbol,
        occurred_on__lte=quarter_end).order_by("-occurred_on").first()
    no_sell = (quarter_number(quarter_end) - quarter_number(last_sell.occurred_on)
               if last_sell else quarter_number(quarter_end) - quarter_number(first_buy.occurred_on))
    if len(_quarterly_closes(ref_spy, quarter_end)) < 2:
        return {"status": "insufficient SPY quarter-end reference history"}
    if len(_quarterly_closes(ref_tqqq, quarter_end)) < 2:
        return {"status": "insufficient TQQQ quarter-end reference history"}
    # Initial holdings may be split into several orders in the first quarter.
    from calendar import monthrange
    first_quarter_last_month = ((first_buy.occurred_on.month - 1) // 3 + 1) * 3
    initial_end = date(first_buy.occurred_on.year, first_quarter_last_month,
                       monthrange(first_buy.occurred_on.year, first_quarter_last_month)[1])
    initial_equity_cost = sum(
        (t.quantity * t.unit_price for t in strategy.ledger.filter(
            symbol=strategy.equity_symbol, kind=LedgerEntry.BUY,
            occurred_on__gte=first_buy.occurred_on, occurred_on__lte=initial_end)),
        ZERO)
    if initial_equity_cost <= ZERO:
        return {"status": "missing initial equity investment"}
    alerts = advice_for_holdings(
        start_date=first_buy.occurred_on, as_of=quarter_end,
        currency=strategy.account.currency,
        equity_symbol=strategy.equity_symbol,
        safe_symbol=strategy.safe_symbol,
        equity_qty=stock_qty, equity_price=closing[strategy.equity_symbol].price,
        safe_qty=safe_qty, safe_price=closing[strategy.safe_symbol].price,
        cash=cash, starting_equity_cost=initial_equity_cost,
        contributions=injected, reference=ref_tqqq, spy_reference=ref_spy,
        prior_skipped_sells=skipped, prior_drawdown=prior_down,
        no_sell_quarters=no_sell,
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
            dispatch(strategy.owner, "signal:%s:%s" % (strategy.pk, signal.signal_key),
                     "Wealtharr — " + signal.title,
                     strategy.name + "\n" + signal.message + "\n" + details +
                     "\nReview this in Wealtharr. No orders have been placed.")
    return {"status": "ok", "created": created, "as_of": quarter_end.isoformat()}
