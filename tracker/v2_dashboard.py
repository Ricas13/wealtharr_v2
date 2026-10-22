"""Read-only figures for the compact dashboard, overview and performance cards.

Everything here is derived from stored owner facts: the effective ledger, the
hourly recorded daily values (StrategyValueSnapshot) and stored Yahoo closes.
Nothing fetches prices, evaluates the strategy or writes to the database.
Index lines are the index's own USD price change, never a simulated portfolio.
"""
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from .core.metrics import VerifiedValuation, evaluate_inception
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


def combine_points(strategies):
    """Sum recorded values across strategies on dates every strategy has."""
    series = [dict((d, (v, n)) for d, v, n in recorded_points(s)) for s in strategies]
    series = [s for s in series if s]
    if not series:
        return []
    common = sorted(set.intersection(*(set(s) for s in series)))
    return [(d, sum((s[d][0] for s in series), ZERO), sum((s[d][1] for s in series), ZERO))
            for d in common]


def growth_index(points):
    """Chain-linked daily growth of £1 with deposits removed (time-weighted)."""
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


def chart(points, *, span, mode, indexes=True):
    """Server-built SVG polylines; no client charting library."""
    today = timezone.localdate()
    start = range_start(span, today)
    shown = [p for p in points if p[0] >= start]
    if len(shown) < 2:
        return {"empty": True, "span": span, "mode": mode}
    first, last = shown[0][0], shown[-1][0]
    days = max((last - first).days, 1)

    def xs(dates):
        return [round((d - first).days / days * CHART_W, 1) for d in dates]

    lines = []
    if mode == "pct":
        growth = growth_index(shown)
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
    ticks = [first + timedelta(days=round(days * i / 4)) for i in range(5)]
    return {
        "empty": False, "span": span, "mode": mode, "lines": lines,
        "y_labels": labels, "zero": zero, "ticks": ticks,
        "first": first, "last": last,
    }


def period_returns(points, today=None):
    """Time-weighted returns from recorded values, and the indexes' own change."""
    today = today or timezone.localdate()
    if not points:
        return ()
    growth = growth_index(points)
    first = points[0][0]
    periods = (("1 week", today - timedelta(days=7)), ("1 month", today - timedelta(days=30)),
               ("3 months", today - timedelta(days=91)), ("6 months", today - timedelta(days=182)),
               ("YTD", date(today.year, 1, 1)), ("1 year", today - timedelta(days=365)),
               ("Since start", first))
    index_data = {s: index_series(s, first - timedelta(days=370)) for s, _ in INDEXES}
    rows = []
    for label, start in periods:
        enough = start >= first
        rows.append({
            "label": label,
            "you": _change(growth, start) if enough else None,
            "indexes": [_change(index_data[s], start) if enough else None for s, _ in INDEXES],
        })
    return tuple(rows)


def monthly_rows(points):
    """Month by month, deposits separate (modified Dietz return)."""
    months = OrderedDict()
    for day, value, net in points:
        months[(day.year, day.month)] = (day, value, net)
    rows = []
    previous_value, previous_net = ZERO, ZERO
    for (year, month), (day, value, net) in months.items():
        deposits = net - previous_net
        gain = value - previous_value - deposits
        base = previous_value + deposits / 2
        rows.append({
            "month": date(year, month, 1), "start": previous_value, "deposits": deposits,
            "end": value, "gain": gain.quantize(PENNY),
            "pct": (gain / base * HUNDRED).quantize(Decimal("0.1")) if base > ZERO else None,
            "partial": (year, month) == (timezone.localdate().year, timezone.localdate().month),
        })
        previous_value, previous_net = value, net
    return tuple(reversed(rows))


def drawdown(points):
    growth = growth_index(points)
    if len(growth) < 2:
        return {"empty": True}
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


def day_change(points):
    if len(points) < 2:
        return None, None
    (_, before, net_before), (_, after, net_after) = points[-2], points[-1]
    change = after - before - (net_after - net_before)
    pct = (change / before * HUNDRED).quantize(Decimal("0.1")) if before > ZERO else None
    return change.quantize(PENNY), pct


def xirr_pct(strategy, points):
    """Money-weighted annual return using the latest recorded value as the close."""
    if not points:
        return None
    day, value, _ = points[-1]
    flows = []
    from .core.metrics import ExternalFlow
    for i, entry in enumerate(e for e in effective_entries(strategy)
                              if e.occurred_on <= day and e.kind in ("DEPOSIT", "WITHDRAW")):
        flows.append(ExternalFlow(
            event_id="entry:%s:v%s" % (entry.pk, entry.version), occurred_on=entry.occurred_on,
            amount_gbp=entry.amount if entry.kind == "DEPOSIT" else -entry.amount, sequence=i))
    if not flows or (day - flows[0].occurred_on).days < 30:
        return None
    result = evaluate_inception(tuple(flows), VerifiedValuation(
        day, value, ("recorded-value:%s:%s" % (strategy.pk, day.isoformat()),), True))
    if result.xirr_annual is None:
        return None
    return (result.xirr_annual * HUNDRED).quantize(Decimal("0.1"))


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
    isa = [s for s in strategies if s.account.kind == "ISA"]
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
    return {
        "email": bool(pref and pref.email_enabled and pref.destination_email),
        "discord": bool(pref and (pref.discord_enabled or pref.discord_webhook_enabled)),
        "telegram": bool(pref and pref.telegram_enabled and pref.telegram_chat_id),
        "last": last,
    }
