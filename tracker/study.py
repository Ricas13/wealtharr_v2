"""Hypothetical Study: what £X (plus optional monthly top-ups) from a past date
would be worth today under 9Sig, buy and hold, SPY and VTI.

Clearly separate from real tracking. It reads StudyPrice only, writes nothing,
and its results never value real holdings or feed instructions or alerts.
9Sig decisions come from the same pure rule function the live reviews use.
Everything is in pounds; US funds are converted with daily GBP/USD rates.

Before LQQ3 existed, growth is rebuilt from QQQ: three times its daily return,
less the fund's ongoing charge, converted to pounds. Before ERNS existed, the
safe sleeve is held as cash at 0%. Periods that include either are flagged.
"""
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from .models import StudyPrice
from .strategies.nine_sig.v2 import _floor_qty, advice_for_holdings, quarter_number

TIMEFRAMES = (("1w", "1 week", 7), ("1m", "1 month", 30), ("ytd", "YTD", None),
              ("1y", "1 year", 365), ("5y", "5 years", 1826), ("10y", "10 years", 3652),
              ("20y", "20 years", 7305))
# WisdomTree NASDAQ 100 3x Daily Leveraged (LQQ3) ongoing charge, used only to
# rebuild its history before launch. Borrowing and swap costs aren't modelled.
GROWTH_CHARGE = 0.0075
SERIES = (("nine_sig", "9Sig"), ("hold", "Buy & hold (100% growth)"),
          ("spy", "SPY (S&P 500)"), ("vti", "VTI (US total market)"))
D = Decimal


@dataclass
class StudyData:
    days: list = field(default_factory=list)
    growth: list = field(default_factory=list)     # GBP per growth unit
    safe: list = field(default_factory=list)       # GBP per safe unit
    spy: list = field(default_factory=list)        # GBP, dividends reinvested
    vti: list = field(default_factory=list)        # GBP, None before launch
    reference: list = field(default_factory=list)  # (date, USD) 3x Nasdaq, for the 100%-up rule
    spy_usd: list = field(default_factory=list)    # (date, USD close), for 30 Down
    growth_real_from: date | None = None
    safe_real_from: date | None = None

    @property
    def ready(self):
        return len(self.days) > 1


def _series(symbol):
    return {r.as_of: (float(r.close), float(r.adj_close))
            for r in StudyPrice.objects.filter(symbol=symbol).only("as_of", "close", "adj_close")}


def load_data():
    """Build the daily GBP series on US trading days that have a GBP/USD rate."""
    qqq, spy, vti = _series("QQQ"), _series("SPY"), _series("VTI")
    fx, lqq3, erns = _series("GBPUSD=X"), _series("LQQ3.L"), _series("ERNS.L")
    data = StudyData()
    if not qqq or not fx or not spy:
        return data
    fx_days = sorted(fx)
    lq_days, er_days = sorted(lqq3), sorted(erns)

    def last_on_or_before(days, values, day, limit=7):
        i = bisect_left(days, day + timedelta(days=1)) - 1
        if i < 0 or (day - days[i]).days > limit:
            return None
        return values[days[i]][1]

    synthetic, reference = [], []
    level, ref = 100.0, 100.0
    prev, last_vti = None, None
    for day in sorted(qqq):
        rate = last_on_or_before(fx_days, fx, day)
        spy_px = spy.get(day)
        if rate is None or spy_px is None:
            continue
        q = qqq[day][1]
        if prev is not None:
            move = q / prev["q"] - 1
            daily = 3 * move - GROWTH_CHARGE / 252
            level *= (1 + daily) * (prev["fx"] / rate)
            ref *= 1 + daily
        prev = {"q": q, "fx": rate}
        data.days.append(day)
        synthetic.append(level)
        reference.append((day, D(str(round(ref, 6)))))
        data.spy.append(spy_px[1] / rate)
        vti_px = vti.get(day)
        if vti_px:
            last_vti = vti_px[1]
        data.vti.append(last_vti / rate if last_vti else None)
        data.spy_usd.append((day, D(str(spy_px[0]))))
    data.reference = reference
    # Splice the real growth fund on from its first price; scale the rebuilt
    # history so the two join without a jump.
    growth = synthetic[:]
    if lq_days:
        start = next((i for i, d in enumerate(data.days) if d >= lq_days[0]), None)
        if start is not None:
            data.growth_real_from = data.days[start]
            first_real = last_on_or_before(lq_days, lqq3, data.days[start])
            scale = first_real / synthetic[start] if first_real else 1.0
            for i in range(start):
                growth[i] = synthetic[i] * scale
            for i in range(start, len(data.days)):
                real = last_on_or_before(lq_days, lqq3, data.days[i])
                growth[i] = real if real else growth[i - 1]
    data.growth = growth
    safe = [1.0] * len(data.days)
    if er_days:
        start = next((i for i, d in enumerate(data.days) if d >= er_days[0]), None)
        if start is not None:
            data.safe_real_from = data.days[start]
            first_real = last_on_or_before(er_days, erns, data.days[start]) or 1.0
            for i in range(len(data.days)):
                if i < start:
                    safe[i] = first_real
                else:
                    real = last_on_or_before(er_days, erns, data.days[i])
                    safe[i] = real if real else safe[i - 1]
    data.safe = safe
    return data


def start_index(data, key, today=None):
    today = today or timezone.localdate()
    days = dict((k, n) for k, _, n in TIMEFRAMES)[key]
    target = date(today.year, 1, 1) if key == "ytd" else today - timedelta(days=days)
    if not data.ready or data.days[0] > target + timedelta(days=7):
        return None
    i = bisect_left(data.days, target)
    return i if i < len(data.days) - 1 else None


def _new_month(days, i):
    return i > 0 and (days[i].year, days[i].month) != (days[i - 1].year, days[i - 1].month)


def _quarter_end(day):
    from calendar import monthrange
    month = ((day.month - 1) // 3 + 1) * 3
    return date(day.year, month, monthrange(day.year, month)[1])


def _buy_and_hold(prices, days, s, amount, monthly):
    if prices[s] is None:
        return None
    units, put_in, values, flows = amount / prices[s], amount, [], [(days[s], amount)]
    for i in range(s, len(days)):
        if i > s and _new_month(days, i) and monthly:
            units += monthly / prices[i]
            put_in += monthly
            flows.append((days[i], monthly))
        values.append(units * prices[i])
    return {"values": values, "flows": flows, "trades": None}


def _nine_sig(data, s, amount, monthly):
    days, growth, safe = data.days, data.growth, data.safe
    g0, s0 = D(str(round(growth[s], 6))), D(str(round(safe[s], 6)))
    equity = _floor_qty(D(amount) * D("0.6"), g0)
    reserve = _floor_qty(D(amount) * D("0.4"), s0)
    cash = D(amount) - equity * g0 - reserve * s0
    start_day, start_cost = days[s], equity * g0
    contributions, down_q, skipped, trades = [], None, 0, 0
    flows, values = [(days[s], amount)], []
    for i in range(s, len(days)):
        day = days[i]
        g, sf = D(str(round(growth[i], 6))), D(str(round(safe[i], 6)))
        if i > s and _new_month(days, i) and monthly:
            cash += D(monthly)
            contributions.append((day, D(monthly)))
            flows.append((day, monthly))
        last_of_quarter = i + 1 < len(days) and quarter_number(days[i + 1]) != quarter_number(day)
        if i > s and last_of_quarter and equity > 0:
            qend = _quarter_end(day)
            review_q = quarter_number(qend)
            k = bisect_left(days, day + timedelta(days=1))
            alerts = advice_for_holdings(
                start_date=start_day, as_of=qend, currency="GBP",
                equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
                equity_qty=equity, equity_price=g, safe_qty=reserve, safe_price=sf,
                cash=cash, starting_equity_cost=start_cost,
                reference=data.reference[max(0, k - 200):k],
                spy_reference=data.spy_usd[max(0, k - 800):k],
                contributions=tuple(contributions),
                prior_skipped_sells=skipped, prior_drawdown=down_q is not None,
                down_age_quarters=(review_q - down_q) if down_q is not None else 0)
            if any(a["kind"] == "THIRTY_DOWN" for a in alerts):
                down_q, skipped = review_q, 0
            plan = next((a for a in alerts if a["kind"] == "QUARTERLY"), None)
            if plan:
                for action in plan["actions"]:
                    qty = D(action["quantity"])
                    if action["side"] == "HOLD":
                        skipped += 1
                    elif action["symbol"] == "LQQ3.L":
                        qty = min(qty, equity) if action["side"] == "SELL" else qty
                        equity += qty if action["side"] == "BUY" else -qty
                        cash += (-qty if action["side"] == "BUY" else qty) * g
                        trades += 1
                    else:
                        qty = min(qty, reserve) if action["side"] == "SELL" else qty
                        reserve += qty if action["side"] == "BUY" else -qty
                        cash += (-qty if action["side"] == "BUY" else qty) * sf
                        trades += 1
                cash = max(cash, D("0"))
                if plan.get("reset_reason"):
                    # A completed 60/40 reset becomes the new starting point.
                    start_day, start_cost = qend, equity * g
                    contributions, down_q, skipped = [], None, 0
        values.append(float(equity * g + reserve * sf + cash))
    return {"values": values, "flows": flows, "trades": trades}


def _xirr(flows, end, value):
    """Annual money-weighted return in %, by bisection (study precision only)."""
    def npv(rate):
        total = value
        for day, amount in flows:
            total -= amount * (1 + rate) ** ((end - day).days / 365.0)
        return total
    low, high = -0.99, 10.0
    if npv(low) * npv(high) > 0:
        return None
    for _ in range(80):
        mid = (low + high) / 2
        if npv(low) * npv(mid) <= 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2 * 100


def _summary(days, s, result):
    values, flows = result["values"], result["flows"]
    put_in = sum(amount for _, amount in flows)
    final = values[-1]
    contributed = []
    total, f = 0.0, 0
    for i in range(s, len(days)):
        while f < len(flows) and flows[f][0] <= days[i]:
            total += flows[f][1]
            f += 1
        contributed.append(total)
    # Deposit-free growth for a fair drawdown.
    peak, level, worst = 1.0, 1.0, 0.0
    for j in range(1, len(values)):
        flow = contributed[j] - contributed[j - 1]
        if values[j - 1] > 0:
            level *= (values[j] - flow) / values[j - 1]
        peak = max(peak, level)
        worst = min(worst, level / peak - 1)
    span = (days[-1] - days[s]).days
    annual = _xirr(flows, days[-1], final) if span >= 360 else None
    return {
        "final": final, "put_in": put_in, "gain": final - put_in,
        "gain_pct": (final / put_in - 1) * 100 if put_in else None,
        "annual": annual, "drawdown": worst * 100, "trades": result["trades"],
        "values": values, "contributed": contributed,
    }


def run(data, key, amount, monthly):
    """All four series for one timeframe, or None when history doesn't reach back."""
    s = start_index(data, key)
    if s is None:
        return None
    raw = {
        "nine_sig": _nine_sig(data, s, amount, monthly),
        "hold": _buy_and_hold(data.growth, data.days, s, amount, monthly),
        "spy": _buy_and_hold(data.spy, data.days, s, amount, monthly),
        "vti": _buy_and_hold(data.vti, data.days, s, amount, monthly),
    }
    out = {name: (_summary(data.days, s, value) if value else None) for name, value in raw.items()}
    start = data.days[s]
    return {
        "start": start, "end": data.days[-1], "series": out,
        "simulated_growth": bool(data.growth_real_from and start < data.growth_real_from)
        or data.growth_real_from is None,
        "simulated_safe": bool(data.safe_real_from and start < data.safe_real_from)
        or data.safe_real_from is None,
        "dates": data.days[s:],
    }
