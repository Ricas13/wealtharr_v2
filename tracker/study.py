"""Hypothetical Study: what £X (plus optional monthly top-ups) from a past date
would be worth today under 9Sig, buy and hold, SPY and VTI.

Clearly separate from real tracking. It reads StudyPrice only, writes nothing,
and its results never value real holdings or feed instructions or alerts.
9Sig decisions come from the same pure rule function the live reviews use.
Everything is in pounds; US funds are converted with daily GBP/USD rates.

Before LQQ3 existed, growth is rebuilt from QQQ: three times its daily return,
less the fund's ongoing charge, converted to pounds. Before ERNS existed, the
safe sleeve is held as cash at 0%. Periods that include either are flagged.
The 100%-up rule reads real TQQQ closes once TQQQ existed, as live reviews do.
A price is never carried forward more than a week: days without a fresh
price for every fund are left out, so the study ends on the last day all its
prices are fresh. Resets are assumed confirmed and filled at the quarter close
(live tracking waits for the owner), and each run reports how many it made.
"""
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from .models import StudyPrice
from .strategies.nine_sig.v2 import _floor_qty, advice_for_holdings, quarter_number
from .strategies.nine_sig.v3 import advice_for_holdings as v3_advice

TIMEFRAMES = (("1w", "1 week", 7), ("1m", "1 month", 30), ("ytd", "YTD", None),
              ("1y", "1 year", 365), ("5y", "5 years", 1826), ("10y", "10 years", 3652),
              ("20y", "20 years", 7305), ("max", "All available", None))
# WisdomTree NASDAQ 100 3x Daily Leveraged (LQQ3) ongoing charge, used only to
# rebuild its history before launch. Borrowing and swap costs aren't modelled.
GROWTH_CHARGE = 0.0075
SERIES = (("nine_sig", "9Sig"), ("hold", "Buy & hold (100% growth)"),
          ("spy", "SPY (S&P 500)"), ("vti", "VTI (US total market)"))
D = Decimal
TITLE = "9Sig"
PRIMARY = "nine_sig"
NEEDS = "QQQ, TQQQ, SPY, VTI, GBP/USD, LQQ3 and ERNS"


@dataclass
class StudyData:
    days: list = field(default_factory=list)
    growth: list = field(default_factory=list)     # GBP per growth unit
    safe: list = field(default_factory=list)       # GBP per safe unit
    spy: list = field(default_factory=list)        # GBP, dividends reinvested
    vti: list = field(default_factory=list)        # GBP, None before launch
    reference: list = field(default_factory=list)  # (date, USD) TQQQ, rebuilt before launch, for 100%-up
    spy_usd: list = field(default_factory=list)    # (date, USD close), for 30 Down
    growth_real_from: date | None = None
    safe_real_from: date | None = None
    reference_real_from: date | None = None
    lines: "Lines | None" = None  # which funds and currency this data is for

    @property
    def ready(self):
        return len(self.days) > 1


@dataclass(frozen=True)
class Lines:
    """One account currency's 9Sig funds for the study."""
    currency: str = "GBP"
    growth: str = "LQQ3.L"
    safe: str = "ERNS.L"
    growth_name: str = "LQQ3"
    safe_name: str = "ERNS"
    rules: str = "v2"  # the pinned rule version for accounts in this currency


GBP_LINES = Lines()


def _series(symbol):
    return {r.as_of: (float(r.close), float(r.adj_close))
            for r in StudyPrice.objects.filter(symbol=symbol).only("as_of", "close", "adj_close")}


def load_data(lines=GBP_LINES):
    """Build the daily series in the account currency on US trading days that have a rate."""
    qqq, spy, vti, tqqq = _series("QQQ"), _series("SPY"), _series("VTI"), _series("TQQQ")
    lqq3, erns = _series(lines.growth), _series(lines.safe)
    # US dollars per unit of account currency (1 for a dollar account).
    fx = ({day: (1.0, 1.0) for day in qqq} if lines.currency == "USD"
          else _series("%sUSD=X" % lines.currency))
    data = StudyData(lines=lines)
    if not qqq or not fx or not spy:
        return data
    fx_days = sorted(fx)
    lq_days, er_days = sorted(lqq3), sorted(erns)
    vti_days, tq_days = sorted(vti), sorted(tqqq)

    def last_on_or_before(days, values, day, limit=7, column=1):
        i = bisect_left(days, day + timedelta(days=1)) - 1
        if i < 0 or (day - days[i]).days > limit:
            return None
        return values[days[i]][column]

    synthetic, reference = [], []
    level, ref = 100.0, 100.0
    prev = None
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
        vti_px = last_on_or_before(vti_days, vti, day)
        data.vti.append(vti_px / rate if vti_px else None)
        data.spy_usd.append((day, D(str(spy_px[0]))))
    data.reference = _real_reference(reference, tqqq, tq_days, data)
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
                growth[i] = last_on_or_before(lq_days, lqq3, data.days[i])
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
                    safe[i] = last_on_or_before(er_days, erns, data.days[i])
    data.safe = safe
    return _fresh_days_only(data)


def _real_reference(rebuilt, tqqq, tq_days, data):
    """TQQQ's own closes from its first price, the rebuilt line scaled to join it before.

    Live reviews compare TQQQ quarter closes; the rebuilt line only stands in
    before TQQQ existed. Days with no TQQQ close within a week are left out.
    """
    if not tq_days:
        return rebuilt
    start = next((i for i, (day, _) in enumerate(rebuilt) if day >= tq_days[0]), None)
    if start is None:
        return rebuilt
    data.reference_real_from = rebuilt[start][0]

    def close_on(day):
        i = bisect_left(tq_days, day + timedelta(days=1)) - 1
        if i < 0 or (day - tq_days[i]).days > 7:
            return None
        return D(str(tqqq[tq_days[i]][0]))

    first = close_on(rebuilt[start][0])
    scale = first / rebuilt[start][1]
    out = [(day, (value * scale).quantize(D("0.000001"))) for day, value in rebuilt[:start]]
    for day, _ in rebuilt[start:]:
        close = close_on(day)
        if close:
            out.append((day, close))
    return out


def _fresh_days_only(data):
    """Leave out days where a fund has no price from the last week (never carry one forward)."""
    keep = [i for i in range(len(data.days))
            if data.growth[i] is not None and data.safe[i] is not None]
    if len(keep) == len(data.days):
        return data
    for name in ("days", "growth", "safe", "spy", "vti"):
        values = getattr(data, name)
        setattr(data, name, [values[i] for i in keep])
    return data


def start_index(data, key, today=None):
    today = today or timezone.localdate()
    days = dict((k, n) for k, _, n in TIMEFRAMES)[key]
    if not data.ready:
        return None
    if key == "max":
        return 0  # All dates for which the selected Study has usable observations.
    target = date(today.year, 1, 1) if key == "ytd" else today - timedelta(days=days)
    if data.days[0] > target + timedelta(days=7):
        return None
    i = bisect_left(data.days, target)
    return i if i < len(data.days) - 1 else None


def _new_month(days, i):
    return i > 0 and (days[i].year, days[i].month) != (days[i - 1].year, days[i - 1].month)


def _quarter_end(day):
    from calendar import monthrange
    month = ((day.month - 1) // 3 + 1) * 3
    return date(day.year, month, monthrange(day.year, month)[1])


# When the Study's rule trades fill: at the close that triggered them (the
# default, a mechanical replay) or at the next trading day's close (closer to
# what someone acting on a close-based signal could actually get).
FILLS = (("close", "Same close"), ("next", "Next day's close"))


def fill_orders(orders, holdings, cash, prices):
    """Fill decided orders at new prices: sells first, buys never beyond cash.

    ``orders`` is [(key, side, quantity)], ``holdings`` {key: Decimal} (updated
    in place), ``prices`` {key: Decimal}. Returns (cash, orders filled).
    """
    from decimal import ROUND_DOWN
    filled = 0
    for key, side, quantity in sorted(orders, key=lambda o: o[1] != "SELL"):
        price = prices[key]
        if side == "SELL":
            quantity = min(quantity, holdings[key])
            holdings[key] -= quantity
            cash += quantity * price
        else:
            affordable = (cash / price).quantize(D("0.0001"), rounding=ROUND_DOWN) if price > 0 else D("0")
            quantity = max(min(quantity, affordable), D("0"))
            holdings[key] += quantity
            cash -= quantity * price
        filled += quantity > 0
    return cash, filled


def _buy_and_hold(prices, days, s, amount, monthly):
    if prices[s] is None:
        return None
    if any(p is None for p in prices[s:]):
        return None  # a gap of over a week: no comparison rather than a stale one
    units, put_in, values, flows = amount / prices[s], amount, [], [(days[s], amount)]
    for i in range(s, len(days)):
        if i > s and _new_month(days, i) and monthly:
            units += monthly / prices[i]
            put_in += monthly
            flows.append((days[i], monthly))
        values.append(units * prices[i])
    return {"values": values, "flows": flows, "trades": None}


def _nine_sig(data, s, amount, monthly, fill="close"):
    days, growth, safe = data.days, data.growth, data.safe
    g0, s0 = D(str(round(growth[s], 6))), D(str(round(safe[s], 6)))
    equity = _floor_qty(D(amount) * D("0.6"), g0)
    reserve = _floor_qty(D(amount) * D("0.4"), s0)
    cash = D(amount) - equity * g0 - reserve * s0
    start_day, start_cost = days[s], equity * g0
    contributions, down_q, skipped, trades, resets = [], None, 0, 0, 0
    flows, values = [(days[s], amount)], []
    pending, pending_reset = [], None
    lines = data.lines or GBP_LINES
    for i in range(s, len(days)):
        day = days[i]
        g, sf = D(str(round(growth[i], 6))), D(str(round(safe[i], 6)))
        if pending:
            # Orders decided at the previous close fill at today's close.
            held = {lines.growth: equity, lines.safe: reserve}
            cash, filled = fill_orders(pending, held, cash, {lines.growth: g, lines.safe: sf})
            equity, reserve, trades, pending = held[lines.growth], held[lines.safe], trades + filled, []
            if pending_reset:
                start_day, start_cost = pending_reset, equity * g
                pending_reset = None
        if i > s and _new_month(days, i) and monthly:
            cash += D(monthly)
            contributions.append((day, D(monthly)))
            flows.append((day, monthly))
        last_of_quarter = i + 1 < len(days) and quarter_number(days[i + 1]) != quarter_number(day)
        if i > s and last_of_quarter and equity > 0:
            qend = _quarter_end(day)
            review_q = quarter_number(qend)
            k = bisect_left(days, day + timedelta(days=1))
            rules = v3_advice if lines.rules == "v3" else advice_for_holdings
            alerts = rules(
                start_date=start_day, as_of=qend, currency=lines.currency,
                equity_symbol=lines.growth, safe_symbol=lines.safe,
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
            if plan and fill == "next":
                skipped += sum(a["side"] == "HOLD" for a in plan["actions"])
                pending = [(a["symbol"], a["side"], D(a["quantity"])) for a in plan["actions"]
                           if a["side"] in ("BUY", "SELL")]
                if plan.get("reset_reason"):
                    pending_reset = qend
                    contributions, down_q, skipped = [], None, 0
                    resets += 1
            elif plan:
                for action in plan["actions"]:
                    qty = D(action["quantity"])
                    if action["side"] == "HOLD":
                        skipped += 1
                    elif action["symbol"] == lines.growth:
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
                    # Modelling assumption: the owner confirms the 60/40 reset
                    # and it fills at the quarter close; it becomes the new
                    # starting point. Live tracking waits for that confirmation.
                    start_day, start_cost = qend, equity * g
                    contributions, down_q, skipped = [], None, 0
                    resets += 1
        values.append(float(equity * g + reserve * sf + cash))
    return {"values": values, "flows": flows, "trades": trades, "resets": resets}


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


def run(data, key, amount, monthly, fill="close"):
    """All four series for one timeframe, or None when history doesn't reach back."""
    s = start_index(data, key)
    if s is None:
        return None
    raw = {
        "nine_sig": _nine_sig(data, s, amount, monthly, fill),
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
        "simulated_notes": _simulated_notes(data, start),
        "assumed_resets": raw["nine_sig"]["resets"],
    }


def _simulated_notes(data, start):
    lines = data.lines or GBP_LINES
    notes = []
    if data.growth_real_from is None or start < data.growth_real_from:
        notes.append("Before %s, the growth fund is rebuilt from 3x QQQ's daily return less %.2f%% a year." % (
            _day(data.growth_real_from, lines.growth_name + " data"), GROWTH_CHARGE * 100))
    if data.safe_real_from is None or start < data.safe_real_from:
        notes.append("Before %s, the safe sleeve is cash at 0%%." % _day(
            data.safe_real_from, lines.safe_name + " data"))
    if data.reference_real_from is None or start < data.reference_real_from:
        notes.append("Before %s, the 100%%-up check uses the rebuilt 3x line instead of TQQQ." % _day(
            data.reference_real_from, "TQQQ data"))
    return notes


def _day(day, default):
    return "%d %s" % (day.day, day.strftime("%b %Y")) if day else default


def how_it_works(data):
    lines = data.lines or GBP_LINES
    money = {"GBP": "pounds", "USD": "US dollars", "EUR": "euros"}[lines.currency]
    return (
        ("9Sig starts 60%% growth (%s) / 40%% safe (%s) and runs the live quarterly rule at each " % (
            lines.growth_name, lines.safe_name)) +
        "quarter end: the 9% signal line, half of new money, the large-buy throttle, 30 Down (from SPY), "
        "the 100%-up reset (from TQQQ's quarter closes) and 60/40 resets.",
        "Assumption: every 60/40 reset is treated as confirmed and filled at that quarter's close. In live "
        "tracking a 100%-up reset is only a review warning and every reset waits for you to confirm it, "
        "so real results can differ. The number of resets in each run is shown with its results.",
        "Monthly top-ups arrive on the first trading day of each month. In 9Sig they wait as cash for "
        "the next quarterly review; in the others they're invested that day.",
        ("All values are in %s. SPY, VTI and QQQ include reinvested dividends%s. Trades fill at that "
         "day's close with no dealing costs or tax." % (
             money, "" if lines.currency == "USD" else
             " and are converted with daily %s/USD rates" % lines.currency)),
        "Rebuilt history is an estimate: real leveraged funds also pay borrowing and swap costs, so early "
        "periods may look better than reality. Buy & hold puts everything, and every top-up, into the growth fund.",
        "A price is never carried forward more than a week. Days without a fresh price for every fund are "
        "left out, so the study ends on the last day all its prices are fresh.",
    )


class NineSigStudy:
    """The 9Sig study for a non-pound account currency (pounds use this module)."""
    TIMEFRAMES = TIMEFRAMES
    SERIES = SERIES
    PRIMARY = PRIMARY
    TITLE = TITLE
    GROWTH_CHARGE = GROWTH_CHARGE

    def __init__(self, lines):
        self.lines = lines
        self.CURRENCY = lines.currency
        self.NEEDS = "QQQ, TQQQ, SPY, VTI, %s%s and %s" % (
            "" if lines.currency == "USD" else "%s/USD, " % lines.currency, lines.growth_name, lines.safe_name)

    def load_data(self):
        return load_data(self.lines)

    def run(self, data, key, amount, monthly, fill="close"):
        return run(data, key, amount, monthly, fill)

    def how_it_works(self, data):
        return how_it_works(data)
