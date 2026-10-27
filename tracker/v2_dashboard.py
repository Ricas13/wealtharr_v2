"""Read-only figures for the compact dashboard, overview and performance cards.

Everything here is derived from stored owner facts: the effective ledger, the
hourly recorded daily values (StrategyValueSnapshot) and stored Yahoo closes.
Nothing fetches prices, evaluates the strategy or writes to the database.
Index lines are the index's own USD price change, never a simulated portfolio.
"""
import json
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import MarketObservation, NotificationDelivery, NotificationPreference
from .v2_ledger_core import effective_entries, replay

ZERO = Decimal("0")
PENNY = Decimal("0.01")
HUNDRED = Decimal("100")
RANGES = (("1m", "1M"), ("3m", "3M"), ("ytd", "YTD"), ("1y", "1Y"), ("all", "All"))
INDEXES = (("QQQ", "Nasdaq-100"), ("SPY", "S&P 500"))
CHART_W = 800
CHART_H = 200


def pick_range(request):
    key = request.GET.get("range", "all")
    return key if key in dict(RANGES) else "all"


def pick_mode(request, default="gbp"):
    mode = request.GET.get("mode", default)
    return mode if mode in ("gbp", "pct") else default


def range_start(key, today):
    if key == "1m":
        return today - timedelta(days=30)
    if key == "3m":
        return today - timedelta(days=91)
    if key == "ytd":
        return date(today.year, 1, 1)
    if key == "1y":
        return today - timedelta(days=365)
    return date(1900, 1, 1)


def recorded_points(strategy):
    """(date, value, net deposits) for each recorded daily value, oldest first."""
    today = timezone.localdate()
    return [
        (row.as_of, row.total_value, row.net_contributed)
        for row in strategy.value_snapshots.filter(as_of__lte=today).order_by("as_of")
    ]


def certified_points(strategy, report=None):
    """Only independently certified historic endpoints may feed performance.

    Saved snapshots remain visible as *records* in History, but a recorded
    number is not inherently market evidence. The latest endpoint must also
    be certified; never leave a historical line looking current after a hold.
    """
    if report is None:
        from .history_data import build_history
        report = build_history(strategy)
    if report["latest_certified"] is None:
        return []
    return [
        (p.as_of, p.value_gbp, p.net_contributed_gbp)
        for p in report["all_points"] if p.certified
    ]


def combine_points(strategies):
    """Sum certified values across strategies on their common dates only."""
    return combine_with_omitted(strategies)[0]


def combine_with_omitted(strategies):
    """(points, names of strategies left out for lack of certified history).

    Never label a subset of strategies as the combined portfolio: when any
    strategy lacks certified points there is no total, and the caller names
    who is missing.
    """
    all_series = [(s, dict((d, (v, n)) for d, v, n in certified_points(s))) for s in strategies]
    omitted = [getattr(s, "name", str(s)) for s, series in all_series if not series]
    series = [series for _, series in all_series if series]
    if omitted or not series:
        return [], omitted
    common = sorted(set.intersection(*(set(s) for s in series)))
    return [(d, sum((s[d][0] for s in series), ZERO), sum((s[d][1] for s in series), ZERO))
            for d in common], omitted


def external_cash_flow_days(strategy):
    """Real external transfers, including offsetting deposits and withdrawals."""
    return frozenset(e.occurred_on for e in effective_entries(strategy)
                     if e.kind in ("DEPOSIT", "WITHDRAW"))


def has_intermediate_flows(points, cash_flow_days=()):
    """Dated transfers need intraday valuation boundaries for exact TWR.

    Net contributions alone are insufficient: a deposit and withdrawal on the
    same date (or in the same valuation interval) can cancel out completely.
    """
    if len(points) < 2:
        return False
    if any(points[i][2] != points[i-1][2] for i in range(1, len(points))):
        return True
    start, end = points[0][0], points[-1][0]
    return any(start < day <= end for day in cash_flow_days)


def growth_index(points, *, cash_flow_days=()):
    """Exact chain of closes only without intermediate external cash transfers.

    Subtracting a same-day deposit from the close and dividing by yesterday\u2019s
    portfolio assumes the deposit occurred at the closing bell. It can double
    the apparent return if the deposit arrived before the market moved.
    Where intraday flow boundaries are unavailable, fail closed rather than
    displaying a fabricated time-weighted return.
    """
    if has_intermediate_flows(points, cash_flow_days=cash_flow_days):
        return []
    result = []
    level = Decimal("1")
    for i, (day, value, net) in enumerate(points):
        if i:
            previous = points[i - 1][1]
            flow = net - points[i - 1][2]
            if previous > ZERO:
                level *= (value - flow) / previous
        result.append((day, level))
    return result


def _change(index, start):
    """Percent change of an index series from its last point on/before start."""
    base = next((v for d, v in reversed(index) if d <= start), None)
    if base is None or not index or base <= ZERO:
        return None
    return ((index[-1][1] / base - 1) * HUNDRED).quantize(Decimal("0.1"))


def index_series(symbol, since):
    return [(row.as_of, row.price) for row in MarketObservation.objects.filter(
        symbol=symbol, currency="USD", as_of__gte=since - timedelta(days=7),
    ).order_by("as_of")]


def _scale(values, lo, hi):
    span = (hi - lo) or Decimal("1")
    return [float((hi - v) / span * CHART_H) for v in values]


def chart(points, *, span, mode, indexes=True, cash_flow_days=()):
    """Server-built SVG polylines; no client charting library."""
    today = timezone.localdate()
    start = range_start(span, today)
    shown = [p for p in points if p[0] >= start]
    if len(shown) < 2:
        return {"empty": True, "span": span, "mode": mode}
    if mode == "pct" and has_intermediate_flows(shown, cash_flow_days=cash_flow_days):
        return {"empty": True, "span": span, "mode": mode,
                "reason": "Percentage return withheld: the selected period includes a deposit or withdrawal without verified pre/post-flow valuations. Use the cash-value chart or annualised XIRR."}
    first, last = shown[0][0], shown[-1][0]
    days = max((last - first).days, 1)

    def xs(dates):
        return [round((d - first).days / days * CHART_W, 1) for d in dates]

    lines = []
    if mode == "pct":
        growth = growth_index(shown, cash_flow_days=cash_flow_days)
        base = growth[0][1]
        series = [("You", "w2-c1", [(d, (v / base - 1) * HUNDRED) for d, v in growth])]
        if indexes:
            for symbol, label in INDEXES:
                rows = [r for r in index_series(symbol, first) if first <= r[0] <= last]
                if len(rows) >= 2:
                    series.append((label, "w2-c2" if symbol == "QQQ" else "w2-c3",
                                   [(d, (p / rows[0][1] - 1) * HUNDRED) for d, p in rows]))
        values = [v for _, _, pts in series for _, v in pts]
        lo, hi = min(values + [ZERO]), max(values + [ZERO])
        pad = (hi - lo) * Decimal("0.08") or Decimal("1")
        lo, hi = lo - pad, hi + pad
        for label, css, pts in series:
            ys = _scale([v for _, v in pts], lo, hi)
            lines.append({"label": label, "css": css, "points": " ".join(
                "%s,%.1f" % (x, y) for x, y in zip(xs([d for d, _ in pts]), ys))})
        labels = ["%+.1f%%" % hi, "%+.1f%%" % ((hi + lo) / 2), "%+.1f%%" % lo]
        zero = _scale([ZERO], lo, hi)[0]
        by_date = [{d: v for d, v in pts} for _, _, pts in series]
        readout = [{"x": xi, "d": d.isoformat(), "t": [
            "%s %+.1f%%" % (series[n][0], by_date[n][d]) for n in range(len(series)) if d in by_date[n]]}
            for xi, (d, _) in zip(xs([d for d, _ in series[0][2]]), series[0][2])]
        underwater = []
    else:
        values = [v for _, v, _ in shown] + [n for _, _, n in shown]
        lo, hi = min(values), max(values)
        pad = (hi - lo) * Decimal("0.08") or Decimal("1")
        lo, hi = max(lo - pad, ZERO), hi + pad
        x = xs([d for d, _, _ in shown])
        for label, css, column in (("Net deposits", "w2-c3", 2), ("Value", "w2-c1", 1)):
            ys = _scale([p[column] for p in shown], lo, hi)
            if column == 2:
                # Deposits are steps, not slopes.
                pts = []
                for i, (xi, yi) in enumerate(zip(x, ys)):
                    if i:
                        pts.append("%s,%.1f" % (xi, ys[i - 1]))
                    pts.append("%s,%.1f" % (xi, yi))
                lines.append({"label": label, "css": css, "points": " ".join(pts)})
            else:
                lines.append({"label": label, "css": css, "points": " ".join(
                    "%s,%.1f" % (a, b) for a, b in zip(x, ys))})
        from .money import current_symbol
        cur = current_symbol()
        labels = [cur + "{:,.0f}".format(v) for v in (hi, (hi + lo) / 2, lo)]
        zero = None
        readout = [{"x": xi, "d": p[0].isoformat(), "t": [
            "Value " + cur + "{:,.0f}".format(p[1]), "Net deposits " + cur + "{:,.0f}".format(p[2])]}
            for xi, p in zip(x, shown)]
        # Shade where the value sits below the money put in (an honest "under
        # water" mark, not a return figure).
        y_value, y_net = _scale([p[1] for p in shown], lo, hi), _scale([p[2] for p in shown], lo, hi)
        underwater = []
        for i in range(1, len(shown)):
            # Money put in is a step: over this stretch it is the earlier day's
            # figure (a deposit on day i only counts from day i). Shade only
            # where the value stays below that level at both ends.
            if shown[i - 1][1] < shown[i - 1][2] and shown[i][1] < shown[i - 1][2]:
                underwater.append("%s,%.1f %s,%.1f %s,%.1f %s,%.1f" % (
                    x[i - 1], y_value[i - 1], x[i], y_value[i], x[i], y_net[i - 1], x[i - 1], y_net[i - 1]))
    ticks = [first + timedelta(days=round(days * i / 4)) for i in range(5)]
    return {
        "empty": False, "span": span, "mode": mode, "lines": lines,
        "y_labels": labels, "zero": zero, "ticks": ticks,
        "first": first, "last": last,
        "readout_json": json.dumps(readout), "underwater": underwater,
    }


def period_returns(points, today=None, *, cash_flow_days=()):
    """Close-to-close returns from recorded values (only periods without transfers), and the indexes' own change."""
    today = today or timezone.localdate()
    if not points:
        return ()
    first = points[0][0]
    periods = (("1 week", today - timedelta(days=7)), ("1 month", today - timedelta(days=30)),
               ("3 months", today - timedelta(days=91)), ("6 months", today - timedelta(days=182)),
               ("YTD", date(today.year, 1, 1)), ("1 year", today - timedelta(days=365)),
               ("Since start", first))
    index_data = {s: index_series(s, first - timedelta(days=370)) for s, _ in INDEXES}
    rows = []
    for label, start in periods:
        enough = start >= first
        anchor = next((p for p in reversed(points) if p[0] <= start), None)
        segment = [p for p in points if anchor and p[0] >= anchor[0]]
        growth = growth_index(segment, cash_flow_days=cash_flow_days) if enough and anchor else []
        rows.append({
            "label": label,
            "you": _change(growth, anchor[0]) if len(growth) >= 2 else None,
            "indexes": [_change(index_data[s], start) if enough else None for s, _ in INDEXES],
        })
    return tuple(rows)


def monthly_rows(points, *, cash_flow_days=()):
    """Month by month, showing exact cash gain; do not guess within-month flow timing."""
    months = OrderedDict()
    for day, value, net in points:
        months[(day.year, day.month)] = (day, value, net)
    rows = []
    flow_months = {(day.year, day.month) for day in cash_flow_days}
    previous_value, previous_net = ZERO, ZERO
    for (year, month), (day, value, net) in months.items():
        deposits = net - previous_net
        gain = value - previous_value - deposits
        base = previous_value + deposits / 2
        rows.append({
            "month": date(year, month, 1), "start": previous_value, "deposits": deposits,
            "end": value, "gain": gain.quantize(PENNY),
            "pct": (gain / base * HUNDRED).quantize(Decimal("0.1"))
            if base > ZERO and deposits == ZERO and (year, month) not in flow_months else None,
            "partial": (year, month) == (timezone.localdate().year, timezone.localdate().month),
        })
        previous_value, previous_net = value, net
    return tuple(reversed(rows))


def drawdown(points, *, cash_flow_days=()):
    growth = growth_index(points, cash_flow_days=cash_flow_days)
    if len(growth) < 2:
        return {"empty": True, "reason": (
            "Exact drawdown needs verified pre/post-deposit valuations; no percentage is inferred."
            if has_intermediate_flows(points, cash_flow_days=cash_flow_days)
            else "Needs at least two verified valuation dates.")}
    peak = growth[0][1]
    values = []
    for _, level in growth:
        peak = max(peak, level)
        values.append((level / peak - 1) * HUNDRED)
    worst = min(values)
    first, last = growth[0][0], growth[-1][0]
    days = max((last - first).days, 1)
    # Keep small dips small: the axis always spans at least 0 to −5%.
    floor = min(worst * Decimal("1.15"), Decimal("-5"))
    pts = " ".join("%s,%.1f" % (round((d - first).days / days * CHART_W, 1),
                                float(v / floor * 120)) for (d, _), v in zip(growth, values))
    return {"empty": False, "points": pts, "area": "0,0 %s %d,0" % (pts, CHART_W),
            "max": worst.quantize(Decimal("0.1")), "current": values[-1].quantize(Decimal("0.1")),
            "first": first, "last": last}


def money_in_by_month(strategies, months=12):
    totals = OrderedDict()
    today = timezone.localdate()
    cursor = date(today.year, today.month, 1)
    for _ in range(months):
        totals[cursor] = ZERO
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    for strategy in strategies:
        for entry in effective_entries(strategy):
            key = entry.occurred_on.replace(day=1)
            if key in totals and entry.kind in ("DEPOSIT", "WITHDRAW"):
                totals[key] += entry.amount if entry.kind == "DEPOSIT" else -entry.amount
    rows = list(reversed(totals.items()))
    # Trim leading empty months before the first deposit.
    while len(rows) > 1 and rows[0][1] == ZERO:
        rows.pop(0)
    top = max((abs(v) for _, v in rows), default=ZERO) or Decimal("1")
    return tuple({"month": m, "amount": v, "height": int(abs(v) / top * 100)} for m, v in rows)


def day_change(points, *, cash_flow_days=()):
    if len(points) < 2:
        return None, None
    (_, before, net_before), (_, after, net_after) = points[-2], points[-1]
    change = after - before - (net_after - net_before)
    pct = ((change / before * HUNDRED).quantize(Decimal("0.1"))
           if before > ZERO and not has_intermediate_flows(
               points[-2:], cash_flow_days=cash_flow_days) else None)
    return change.quantize(PENNY), pct


def xirr_pct(strategy, points=None):
    """Money-weighted annual return, only from History's source-checked valuation.

    Every page shows the same figure: the Performance page's inception XIRR,
    which needs a certified close for the latest value (not just a saved
    snapshot). ``points`` is unused and kept for older callers.
    """
    from .history_data import build_history
    report = build_history(strategy)
    latest, flows = report["latest_certified"], report["flows"]
    if report.get("xirr_pct") is None or latest is None or not flows:
        return None
    if (latest.as_of - min(f.occurred_on for f in flows)).days < 30:
        return None
    return report["xirr_pct"].quantize(Decimal("0.1"))


def best_worst(monthly):
    full = [r for r in monthly if r["pct"] is not None and r["start"] > ZERO]
    if not full:
        return None, None
    return max(full, key=lambda r: r["pct"]), min(full, key=lambda r: r["pct"])


def holdings_detail(strategy, report, roles):
    """Shares, latest price, day move, value and P/L on average cost."""
    cost = {}
    shares = {}
    for entry in effective_entries(strategy):
        if entry.kind == "BUY":
            cost[entry.symbol] = cost.get(entry.symbol, ZERO) + entry.quantity * entry.unit_price + entry.fee
            shares[entry.symbol] = shares.get(entry.symbol, ZERO) + entry.quantity
        elif entry.kind == "SELL" and shares.get(entry.symbol):
            cost[entry.symbol] -= cost[entry.symbol] * entry.quantity / shares[entry.symbol]
            shares[entry.symbol] -= entry.quantity
        elif entry.kind == "SPLIT" and shares.get(entry.symbol):
            shares[entry.symbol] *= entry.quantity  # same total cost, more (or fewer) shares
    rows = []
    order = {symbol: i for i, symbol in enumerate(roles)}
    for symbol, quantity in sorted(report["positions"].items(),
                                   key=lambda item: (order.get(item[0], 99), item[0])):
        if quantity <= ZERO:
            continue
        mark = report["marks"].get(symbol)
        closes = list(MarketObservation.objects.filter(
            symbol=symbol, currency=strategy.account.currency).order_by("-as_of")[:2])
        move = None
        if len(closes) == 2 and closes[1].price > ZERO:
            move = ((closes[0].price / closes[1].price - 1) * HUNDRED).quantize(Decimal("0.1"))
        value = quantity * mark.price if mark else None
        rows.append({
            "symbol": symbol, "role": roles.get(symbol, "Other"), "quantity": quantity,
            "price": mark.price if mark else None, "as_of": mark.as_of if mark else None,
            "move": move, "value": value,
            "pl": (value - cost.get(symbol, ZERO)).quantize(PENNY) if value is not None else None,
        })
    return tuple(rows)


def allocation(report, lines, roles, targets=None):
    """Current split against the strategy's weights (or ``targets``: {symbol: 0..1})."""
    if report["value"] is None or report["value"] <= ZERO:
        return None
    total = report["value"]
    weights = ({s: w * HUNDRED for s, w in targets.items()} if targets is not None
               else {line.ticker: line.weight_pct for line in lines})
    rows = []
    # Funds in the strategy's own order (roles is ordered), then anything else.
    order = {symbol: i for i, symbol in enumerate(roles)}
    for symbol, quantity in sorted(report["positions"].items(),
                                   key=lambda item: (order.get(item[0], 99), item[0])):
        mark = report["marks"].get(symbol)
        if quantity <= ZERO or mark is None:
            continue
        value = quantity * mark.price
        rows.append({"label": roles.get(symbol, symbol), "symbol": symbol, "value": value,
                     "pct": (value / total * HUNDRED).quantize(Decimal("0.1")),
                     "target": weights.get(symbol)})
    rows.append({"label": "Cash", "symbol": "", "value": report["cash"],
                 "pct": (report["cash"] / total * HUNDRED).quantize(Decimal("0.1")),
                 "target": None})
    palette = ("w2-c1", "w2-c2", "w2-c3", "w2-c5", "w2-c6", "w2-c7")  # w2-c4 is cash grey
    end = 0.0
    for i, row in enumerate(rows):
        row["css"] = palette[i % len(palette)] if row["symbol"] else "w2-c4"
        row["width"] = max(min(float(row["pct"]), 100.0), 0.0)
        # Where this fund's target share ends on the bar, for a marker.
        if row["target"] is not None:
            end += float(row["target"])
            row["target_end"] = end if end < 99.5 else None
    return tuple(rows)


def recent_entries(strategy, count=4):
    rows = sorted(effective_entries(strategy), key=lambda e: (e.occurred_on, e.pk), reverse=True)
    result = []
    for entry in rows[:count]:
        amount = entry.amount if entry.kind not in ("BUY", "SELL") else entry.financial_value
        label = {"DEPOSIT": "Deposit", "WITHDRAW": "Withdrawal", "DIVIDEND": "Dividend",
                 "INTEREST": "Interest", "FEE": "Fee"}.get(entry.kind)
        if label is None:
            label = "%s %s" % (entry.kind.title(), entry.symbol)
        result.append({"date": entry.occurred_on, "label": label, "amount": amount})
    return tuple(result)


def isa_allowance(strategies):
    from .core.isa_allowance import OVERALL_LIMIT_2026_GBP, estimate_allowance, tax_year_bounds
    from .v2_onboarding import OPENING_NOTE
    # The allowance is a statutory pound amount: only pound ISA accounts count.
    isa = [s for s in strategies if s.account.kind == "ISA" and s.account.currency == "GBP"]
    if not isa:
        return None
    today = timezone.localdate()
    start, end = tax_year_bounds(today)
    deposits = tuple(
        (e.occurred_on, e.amount) for s in isa for e in effective_entries(s)
        if e.kind == "DEPOSIT" and start <= e.occurred_on <= end and e.note != OPENING_NOTE
    )
    try:
        estimate = estimate_allowance(as_of=today, recorded_deposits=deposits)
    except ValueError:
        return None
    used = estimate.known_total_gbp
    limit = OVERALL_LIMIT_2026_GBP
    return {"label": estimate.tax_year_label, "used": used,
            "left": max(limit - used, ZERO), "limit": limit,
            "resets": end + timedelta(days=1),
            "width": min(float(used / limit * 100), 100.0),
            "status": estimate.status}


def alerts_summary(owner, strategy=None):
    pref = NotificationPreference.objects.filter(owner=owner).first()
    deliveries = NotificationDelivery.objects.filter(owner=owner)
    if strategy is not None:
        deliveries = deliveries.filter(strategy=strategy)
    last = deliveries.order_by("-created_at", "-pk").first()
    sent = deliveries.filter(status="SENT").order_by("-created_at", "-pk").first()
    return {
        "email": bool(pref and pref.email_enabled and pref.destination_email),
        "discord": bool(pref and (pref.discord_enabled or pref.discord_webhook_enabled)),
        "telegram": bool(pref and pref.telegram_enabled and pref.telegram_chat_id),
        "last": last,
        "last_sent": sent,
    }
