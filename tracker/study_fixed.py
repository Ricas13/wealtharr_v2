"""Hypothetical Study for fixed-weight strategies (All Weather, Golden Butterfly, ...).

Same contract as the 9Sig study (`tracker/study.py`): reads StudyPrice only,
writes nothing, never values real holdings or feeds instructions or alerts.
Each year end replays the strategy's own pure ``evaluate``, so the study
follows the live rules. Everything is in pounds; USD series are converted
with that day's GBP/USD close.

Before a fund existed, its role follows a named stand-in (e.g. SPY for an
S&P 500 fund), scaled to join the real fund without a jump; a role with no
stand-in is held as cash at 0%. Periods using a stand-in are flagged.
"""
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .models import StudyPrice
from .study import TIMEFRAMES, _buy_and_hold, _new_month, _summary, start_index  # noqa: F401

D = Decimal
CASH = "CASH"


@dataclass(frozen=True)
class Source:
    """Where one role's history comes from."""
    fund: str                  # the pinned fund's Yahoo symbol
    fund_label: str
    proxy: str = CASH          # stand-in symbol before the fund existed, or CASH
    proxy_label: str = "cash at 0%"
    # False for a GBP-hedged fund: its stand-in's US-dollar returns are used as
    # they are, since hedging removes most of the currency effect.
    proxy_in_gbp: bool = True


@dataclass
class FixedData:
    days: list = field(default_factory=list)
    prices: dict = field(default_factory=dict)     # role -> [GBP per unit]
    spy: list = field(default_factory=list)
    vti: list = field(default_factory=list)
    real_from: dict = field(default_factory=dict)  # role -> first day of the real fund

    @property
    def ready(self):
        return len(self.days) > 1


def _gbp_series(symbol, fx_days, fx, convert=True):
    """{day: GBP adjusted close} for one StudyPrice symbol (or local returns if not ``convert``)."""
    rows = StudyPrice.objects.filter(symbol=symbol).only("as_of", "adj_close", "currency")
    out = {}
    for r in rows:
        value = float(r.adj_close)
        if r.currency == "USD" and convert:
            i = bisect_left(fx_days, r.as_of + timedelta(days=1)) - 1
            if i < 0 or (r.as_of - fx_days[i]).days > 7:
                continue
            value /= fx[fx_days[i]]
        out[r.as_of] = value
    return out


def _last(days, values, day, limit=7):
    i = bisect_left(days, day + timedelta(days=1)) - 1
    if i < 0 or (day - days[i]).days > limit:
        return None
    return values[days[i]]


class FixedWeightStudy:
    """One study engine per fixed-weight strategy; the view only uses this interface."""
    TIMEFRAMES = TIMEFRAMES
    PRIMARY = "strategy"

    def __init__(self, *, title, rules, sources, review_word="yearly"):
        self.TITLE = title
        self.rules = rules
        self.sources = sources  # {role: Source}
        self.SERIES = (("strategy", title), ("spy", "SPY (S&P 500)"), ("vti", "VTI (US total market)"))
        self.review_word = review_word

    @property
    def NEEDS(self):  # noqa: N802 (same interface as the 9Sig study module)
        names = [s.fund.replace(".L", "") for s in self.sources.values()]
        names += [s.proxy for s in self.sources.values() if s.proxy != CASH]
        return ", ".join(dict.fromkeys(names + ["SPY", "VTI", "GBP/USD"]))

    def load_data(self):
        fx = {r.as_of: float(r.adj_close) for r in StudyPrice.objects.filter(symbol="GBPUSD=X")}
        fx_days = sorted(fx)
        data = FixedData()
        if not fx:
            return data
        spy, vti = _gbp_series("SPY", fx_days, fx), _gbp_series("VTI", fx_days, fx)
        if not spy:
            return data
        spy_days, vti_days = sorted(spy), sorted(vti)
        series = {}
        for role, source in self.sources.items():
            fund = _gbp_series(source.fund, fx_days, fx)
            proxy = {} if source.proxy == CASH else _gbp_series(
                source.proxy, fx_days, fx, convert=source.proxy_in_gbp)
            series[role] = (fund, sorted(fund), proxy, sorted(proxy))
        grid = [d for d in spy_days if fx_days and _last(fx_days, fx, d) is not None]
        columns = {}
        for role, (fund, fund_days, proxy, proxy_days) in series.items():
            column = [None] * len(grid)
            start = next((i for i, d in enumerate(grid) if fund_days and d >= fund_days[0]), None)
            if start is not None:
                data.real_from[role] = grid[start]
                for i in range(start, len(grid)):
                    column[i] = _last(fund_days, fund, grid[i]) or column[i - 1]
                first_real = column[start]
                if self.sources[role].proxy == CASH:
                    for i in range(start):
                        column[i] = first_real
                elif proxy_days:
                    anchor = _last(proxy_days, proxy, grid[start])
                    if anchor:
                        scale = first_real / anchor
                        for i in range(start):
                            p = _last(proxy_days, proxy, grid[i])
                            column[i] = p * scale if p else None
            columns[role] = column
        # The study starts once every role has a price (real or stand-in).
        first = next((i for i in range(len(grid))
                      if all(columns[r][i] for r in columns)), None)
        if first is None:
            return data
        keep = [i for i in range(first, len(grid)) if all(columns[r][i] for r in columns)]
        data.days = [grid[i] for i in keep]
        data.prices = {r: [columns[r][i] for i in keep] for r in columns}
        last_vti = None
        for d in data.days:
            data.spy.append(_last(spy_days, spy, d))
            v = vti.get(d)
            last_vti = v if v else last_vti
            data.vti.append(last_vti)
        return data

    def _strategy(self, data, s, amount, monthly):
        rules, days = self.rules, data.days
        weights = dict(rules.WEIGHTS)
        roles = [r for r, _ in rules.WEIGHTS]
        funds = tuple(rules.Fund(r, r, weights[r]) for r in roles)

        def quotes(i):
            return {r: rules.Quote(r, D(str(round(data.prices[r][i], 6))), days[i]) for r in roles}

        positions = {r: D("0") for r in roles}
        cash = D(str(amount))
        trades = 0

        def apply(result):
            nonlocal cash, trades
            for t in result.trades:
                if t.side == "BUY":
                    positions[t.role] += t.quantity
                    cash -= t.quantity * t.price_gbp
                else:
                    positions[t.role] -= t.quantity
                    cash += t.quantity * t.price_gbp
                trades += 1
            cash = max(cash, D("0"))

        # Opening buys at the weights, as setup suggests.
        apply(rules.evaluate(funds=funds, positions=positions, cash=cash, quotes=quotes(s), as_of=days[s]))
        flows, values = [(days[s], amount)], []
        for i in range(s, len(days)):
            if i > s and _new_month(days, i) and monthly:
                cash += D(str(monthly))
                flows.append((days[i], monthly))
            year_end = i + 1 < len(days) and days[i + 1].year != days[i].year
            if i > s and year_end:
                apply(rules.evaluate(funds=funds, positions=positions, cash=cash,
                                     quotes=quotes(i), as_of=date(days[i].year, 12, 31)))
            values.append(float(cash + sum(positions[r] * D(str(round(data.prices[r][i], 6))) for r in roles)))
        return {"values": values, "flows": flows, "trades": trades}

    def run(self, data, key, amount, monthly):
        s = start_index(data, key)
        if s is None:
            return None
        raw = {
            "strategy": self._strategy(data, s, amount, monthly),
            "spy": _buy_and_hold(data.spy, data.days, s, amount, monthly),
            "vti": _buy_and_hold(data.vti, data.days, s, amount, monthly),
        }
        start = data.days[s]
        notes = []
        for role, source in self.sources.items():
            real = data.real_from.get(role)
            if real is None or start < real:
                notes.append("Before %s, %s follows %s." % (
                    "%d %s" % (real.day, real.strftime("%b %Y")) if real else "its first price",
                    source.fund_label, source.proxy_label))
        return {
            "start": start, "end": data.days[-1], "dates": data.days[s:],
            "series": {n: (_summary(data.days, s, v) if v else None) for n, v in raw.items()},
            "simulated_notes": notes,
        }

    def how_it_works(self, data):
        weights = ", ".join("%s%% %s" % ((w * 100).normalize(), self.sources[r].fund_label)
                            for r, w in self.rules.WEIGHTS)
        return (
            "Starts at the published weights (%s) and runs the live %s rule after each year end: "
            "sell what's above its weight, buy what's below, never borrowing." % (weights, self.review_word),
            "Monthly top-ups arrive on the first trading day of each month and wait as cash for the "
            "next yearly review; SPY and VTI invest them that day.",
            "All values are in pounds with dividends reinvested. US-dollar series are converted with "
            "daily GBP/USD rates. Trades fill at that day's close with no dealing costs or tax.",
            "Before a fund existed, its role follows a stand-in, flagged on the page.",
        )
