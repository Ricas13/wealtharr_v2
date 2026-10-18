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


def advice_for_holdings(*, start_date, as_of, currency, equity_symbol, safe_symbol,
                        equity_qty, equity_price, safe_qty, safe_price, cash,
                        starting_equity_cost, reference):
    """Pure, deterministic preliminary 9Sig calculations; outputs do not execute trades."""
    if currency != "GBP" or equity_qty <= ZERO or starting_equity_cost <= ZERO:
        return []
    if equity_price <= ZERO or safe_price <= ZERO or not reference:
        return []
    alerts = []
    reference = sorted(
        (day, amount) for day, amount in reference if start_date <= day <= as_of and amount > ZERO
    )
    if len(reference) >= 2:
        peak_day, peak_value = max(reference, key=lambda pair: pair[1])
        last_day, last_value = reference[-1]
        decline = Decimal(1) - last_value / peak_value
        if decline >= Decimal("0.30") and last_day > peak_day:
            sell = (equity_qty / Decimal(2)).quantize(PRECISION, rounding=ROUND_DOWN)
            if sell > ZERO:
                alerts.append({
                    "kind": "THIRTY_DOWN", "key": "30down:" + peak_day.isoformat(),
                    "title": "30% reference drawdown: review half a position",
                    "message": (
                        "TQQQ close is %.2f%% under the highest closing price recorded since tracking started (%s). "
                        "Consider the model's half-equity sale rule; verify exceptional cases and actual UK instrument exposure."
                    ) % (float(decline * 100), peak_day.isoformat()),
                    "actions": [{"side": "SELL", "symbol": equity_symbol, "quantity": str(sell),
                                 "estimated_gbp": str((sell * equity_price).quantize(CENTS))}],
                })
    quarters = quarter_number(as_of) - quarter_number(start_date)
    if quarters <= 0:
        return alerts
    target = starting_equity_cost * (Decimal("1.09") ** (Decimal(quarters) / Decimal(4)))
    current_equity = equity_qty * equity_price
    safe_value = safe_qty * safe_price
    diff = target - current_equity
    tolerance = max(Decimal("1"), (cash + current_equity + safe_value) * Decimal("0.005"))
    actions = []
    if diff < -tolerance:
        qty = (abs(diff) / equity_price).quantize(PRECISION, rounding=ROUND_DOWN)
        qty = min(qty, equity_qty)
        if qty > ZERO:
            actions.append({"side": "SELL", "symbol": equity_symbol, "quantity": str(qty),
                            "estimated_gbp": str((qty * equity_price).quantize(CENTS))})
        note = "Equity value is above its quarterly growth line."
    elif diff > tolerance:
        allowed_safe = safe_value * Decimal("0.90")
        budget = min(diff, cash + allowed_safe)
        safe_sale = max(ZERO, budget - cash)
        if safe_sale > ZERO and safe_qty > ZERO:
            qty = min(safe_qty, (safe_sale / safe_price).quantize(PRECISION, rounding=ROUND_DOWN))
            if qty > ZERO:
                actions.append({"side": "SELL", "symbol": safe_symbol, "quantity": str(qty),
                                "estimated_gbp": str((qty * safe_price).quantize(CENTS))})
        funded = cash + sum(Decimal(x["estimated_gbp"]) for x in actions)
        qty = (min(diff, funded) / equity_price).quantize(PRECISION, rounding=ROUND_DOWN)
        if qty > ZERO:
            actions.append({"side": "BUY", "symbol": equity_symbol, "quantity": str(qty),
                            "estimated_gbp": str((qty * equity_price).quantize(CENTS))})
        note = ("The suggested buy is limited by cash and a 10% protected safe-asset reserve."
                if budget < diff else "Equity value is below its quarterly growth line.")
    else:
        note = "Equity value is close to the quarterly growth line; no trade suggested."
    alerts.append({
        "kind": "QUARTERLY", "key": "quarter:" + prior_quarter_name(as_of),
        "title": "Quarterly 9Sig-style review — " + prior_quarter_name(as_of),
        "message": (note + " Target equity value: £%s (9%% annual compounded growth). "
                    "This preliminary engine does not reproduce all skip/reset/shortage rules."
                    % target.quantize(CENTS)),
        "actions": actions,
    })
    return alerts


def review_strategy(strategy):
    """Synchronise existing GBP positions, compute and persist review-only signals."""
    if not strategy.market_data_enabled or not strategy.preset.active:
        return {"status": "disabled"}
    if strategy.preset.slug != "nasdaq-quarterly-target":
        return {"status": "unsupported"}
    if not TICKER.fullmatch(strategy.equity_symbol) or not TICKER.fullmatch(strategy.safe_symbol):
        return {"status": "invalid symbol"}
    report = ledger_summary(strategy)
    if not report["positions"]:
        return {"status": "waiting for first purchase"}
    tickers = [strategy.equity_symbol, strategy.safe_symbol, "TQQQ"]
    data = {symbol: sync_ticker(symbol) for symbol in set(tickers)}
    now = timezone.localdate()
    for symbol in (strategy.equity_symbol, strategy.safe_symbol):
        close = data.get(symbol)
        if close and close.currency == "GBP":
            previous = PriceMark.objects.filter(strategy=strategy, symbol=symbol).first()
            if not previous or previous.as_of <= close.as_of:
                PriceMark.objects.update_or_create(strategy=strategy, symbol=symbol,
                                                  defaults={"price": close.price, "as_of": close.as_of,
                                                            "source": "yahoo"})
    report = ledger_summary(strategy)
    marked = report["marks"]
    prices = [marked.get(symbol) for symbol in (strategy.equity_symbol, strategy.safe_symbol)]
    reference = data.get("TQQQ")
    if (not reference or reference.as_of < now - timedelta(days=8) or
            any(not mark or mark.source != "yahoo" or mark.as_of < now - timedelta(days=8)
                for mark in prices)):
        return {"status": "unverified/stale data"}
    first_buy = strategy.ledger.filter(kind=LedgerEntry.BUY, symbol=strategy.equity_symbol).order_by(
        "occurred_on", "pk").first()
    if not first_buy:
        return {"status": "waiting for equity purchase"}
    as_of = min(prices[0].as_of, prices[1].as_of, reference.as_of)
    history = list(MarketObservation.objects.filter(symbol="TQQQ", as_of__gte=first_buy.occurred_on,
                                                    as_of__lte=as_of).order_by("as_of")
                   .values_list("as_of", "price"))
    alerts = advice_for_holdings(
        start_date=first_buy.occurred_on, as_of=as_of, currency=strategy.account.currency,
        equity_symbol=strategy.equity_symbol, safe_symbol=strategy.safe_symbol,
        equity_qty=report["positions"].get(strategy.equity_symbol, ZERO),
        equity_price=prices[0].price,
        safe_qty=report["positions"].get(strategy.safe_symbol, ZERO),
        safe_price=prices[1].price, cash=report["cash"],
        starting_equity_cost=first_buy.quantity * first_buy.unit_price, reference=history,
    )
    created = 0
    for alert in alerts:
        signal, new = StrategySignal.objects.get_or_create(
            strategy=strategy, signal_key=alert["key"],
            defaults={"kind": alert["kind"], "title": alert["title"], "message": alert["message"],
                      "actions": alert["actions"], "as_of": as_of},
        )
        if new:
            created += 1
            details = "\n".join(
                "%s %s shares %s (estimated £%s)" % (x["side"], x["quantity"], x["symbol"], x["estimated_gbp"])
                for x in alert["actions"]
            )
            dispatch(strategy.owner, "signal:%s:%s" % (strategy.pk, signal.signal_key),
                     "Wealtharr — " + signal.title,
                     strategy.name + "\n" + signal.message + "\n" + details +
                     "\nReview this in Wealtharr. No orders have been placed.")
    return {"status": "ok", "created": created, "as_of": as_of.isoformat()}
