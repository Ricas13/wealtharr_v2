"""Hypothetical Study for fixed-weight strategies (All Weather, Golden Butterfly, ...).

Same contract as the 9Sig study (`tracker/study.py`): reads StudyPrice only,
writes nothing, never values real holdings or feeds instructions or alerts.
Each year end replays the strategy's own pure ``evaluate``, so the study
follows the live rules. Everything is in pounds; USD series are converted
with that day's GBP/USD close.

Before a fund existed, its role follows a named stand-in (e.g. SPY for an
S&P 500 fund), scaled to join the real fund without a jump; a role with no
stand-in is held as cash at 0%. Periods using a stand-in are flagged.
A price is never carried forward more than a week: days without a fresh price
for every fund are left out, so the study ends on the last day all its prices
are fresh.
"""
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN

from .core.calendar import month_end_on_or_after, quarter_end_on_or_after
from .models import StudyPrice
from .study import TIMEFRAMES, _buy_and_hold, _new_month, _summary, fill_orders, start_index  # noqa: F401

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
    # A leveraged fund before launch: this many times the stand-in's daily
    # return (in its own currency), less ``proxy_charge`` a year. Borrowing and
    # swap costs aren't modelled, so rebuilt periods can look better than reality.
    proxy_leverage: int = 1
    proxy_charge: float = 0.0


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


def _gbp_series(symbol, fx_days, fx, convert=True, target="GBP"):
    """{day: adjusted close in ``target``} for one StudyPrice symbol.

    US-dollar rows are divided by that day's target/USD rate (``fx``) unless
    ``convert`` is False (local returns); rows in any other currency are skipped.
    """
    rows = StudyPrice.objects.filter(symbol=symbol).only("as_of", "adj_close", "currency")
    out = {}
    for r in rows:
        value = float(r.adj_close)
        if r.currency != target and r.currency != "USD":
            continue
        if r.currency == "USD" and target != "USD" and convert:
            i = bisect_left(fx_days, r.as_of + timedelta(days=1)) - 1
            if i < 0 or (r.as_of - fx_days[i]).days > 7:
                continue
            value /= fx[fx_days[i]]
        out[r.as_of] = value
    return out


def _leveraged(native, source, fx_days, fx, target):
    """{day: price in ``target``} rebuilt as ``proxy_leverage`` x the stand-in's daily return.

    The leverage applies to the stand-in's own (US-dollar) returns; the result
    is then converted like any dollar series, so currency moves aren't leveraged.
    """
    level, prev, out = 100.0, None, {}
    for day in sorted(native):
        price = native[day]
        if prev is not None:
            level *= 1 + source.proxy_leverage * (price / prev - 1) - source.proxy_charge / 252
        prev = price
        if target == "USD" or not source.proxy_in_gbp:
            out[day] = level
            continue
        i = bisect_left(fx_days, day + timedelta(days=1)) - 1
        if i >= 0 and (day - fx_days[i]).days <= 7:
            out[day] = level / fx[fx_days[i]]
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

    def __init__(self, *, title, rules, sources, review_word="yearly", currency="GBP", rule_text=""):
        self.TITLE = title
        self.rule_text = rule_text
        self.CURRENCY = currency
        self.rules = rules
        # Study and live use the same source-controlled contribution policy.
        self.rules_name = {
            "all_weather": "all-weather", "golden_butterfly": "golden-butterfly",
            "hfea": "hfea", "permanent_portfolio": "permanent-portfolio",
        }.get(rules.__name__.split(".")[-2], "")
        self.sources = sources  # {role: Source}
        self.SERIES = (("strategy", title), ("spy", "SPY (S&P 500)"), ("vti", "VTI (US total market)"))
        self.review_word = review_word

    @property
    def NEEDS(self):  # noqa: N802 (same interface as the 9Sig study module)
        names = [s.fund.replace(".L", "") for s in self.sources.values()]
        names += [s.proxy for s in self.sources.values() if s.proxy != CASH]
        fx = [] if self.CURRENCY == "USD" else ["%s/USD" % self.CURRENCY]
        return ", ".join(dict.fromkeys(names + ["SPY", "VTI"] + fx))

    def load_data(self):
        target = self.CURRENCY
        if target == "USD":
            fx, fx_days = {}, []
        else:
            fx = {r.as_of: float(r.adj_close)
                  for r in StudyPrice.objects.filter(symbol="%sUSD=X" % target)}
            fx_days = sorted(fx)
        data = FixedData()
        if target != "USD" and not fx:
            return data

        def series_of(symbol, convert=True):
            return _gbp_series(symbol, fx_days, fx, convert=convert, target=target)

        spy, vti = series_of("SPY"), series_of("VTI")
        if not spy:
            return data
        spy_days, vti_days = sorted(spy), sorted(vti)
        series = {}
        for role, source in self.sources.items():
            fund = series_of(source.fund)
            if source.proxy == CASH:
                proxy = {}
            elif source.proxy_leverage > 1:
                proxy = _leveraged(series_of(source.proxy, convert=False), source,
                                   fx_days, fx, target)
            else:
                proxy = series_of(source.proxy, convert=source.proxy_in_gbp)
            series[role] = (fund, sorted(fund), proxy, sorted(proxy))
        grid = [d for d in spy_days if target == "USD" or _last(fx_days, fx, d) is not None]
        columns = {}
        for role, (fund, fund_days, proxy, proxy_days) in series.items():
            column = [None] * len(grid)
            start = next((i for i, d in enumerate(grid) if fund_days and d >= fund_days[0]), None)
            if start is not None:
                data.real_from[role] = grid[start]
                for i in range(start, len(grid)):
                    column[i] = _last(fund_days, fund, grid[i])  # None when over a week old
                first_real = column[start]
                if self.sources[role].proxy == CASH:
                    for i in range(start):
                        column[i] = first_real
                elif proxy_days:
                    anchor = _last(proxy_days, proxy, grid[start])
                    if anchor and first_real:
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
        for d in data.days:
            data.spy.append(_last(spy_days, spy, d))
            data.vti.append(_last(vti_days, vti, d))
        return data

    def _strategy(self, data, s, amount, monthly, fill="close"):
        rules, days = self.rules, data.days
        weights = dict(rules.WEIGHTS)
        roles = [r for r, _ in rules.WEIGHTS]
        funds = tuple(rules.Fund(r, r, weights[r]) for r in roles)

        def quotes(i):
            return {r: rules.Quote(r, D(str(round(data.prices[r][i], 6))), days[i]) for r in roles}

        positions = {r: D("0") for r in roles}
        cash = D(str(amount))
        trades = 0
        pending = []

        def apply(result, now=False):
            nonlocal cash, trades, pending
            if fill == "next" and not now:
                pending = [(t.role, t.side, t.quantity) for t in result.trades]
                return
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
        apply(rules.evaluate(funds=funds, positions=positions, cash=cash, quotes=quotes(s), as_of=days[s]),
              now=True)
        flows, values = [(days[s], amount)], []
        for i in range(s, len(days)):
            if pending:
                cash, filled = fill_orders(pending, positions, cash, {r: q.price_gbp for r, q in quotes(i).items()})
                trades, pending = trades + filled, []
            if i > s and _new_month(days, i) and monthly:
                from .strategies.contributions import allocation
                cash += D(str(monthly))
                flows.append((days[i], monthly))
                # Invest new cash into underweight funds, without moving
                # existing holdings. Yearly/quarterly rebalance stays dated.
                prices = quotes(i)
                values_now = {r: positions[r] * prices[r].price_gbp for r in roles}
                buys = allocation(
                    self.rules_name, D(str(monthly)), values=values_now, weights=weights)
                for role, budget in buys.items():
                    price = prices[role].price_gbp
                    qty = (budget / price).quantize(D("0.0001"), rounding=ROUND_DOWN)
                    if qty > 0 and qty * price >= D("1"):
                        if fill == "next" and i + 1 < len(days):
                            # Same rule as every other order: decided on this
                            # close, filled at the next one.
                            pending = pending + [(role, "BUY", qty)]
                        else:
                            positions[role] += qty
                            cash -= qty * price
                            trades += 1
            if self.review_word == "monthly":
                due = i + 1 < len(days) and days[i + 1].month != days[i].month
                review_day = month_end_on_or_after(days[i])
            elif self.review_word == "quarterly":
                due = i + 1 < len(days) and (days[i + 1].month - 1) // 3 != (days[i].month - 1) // 3
                review_day = quarter_end_on_or_after(days[i])
            else:
                due = i + 1 < len(days) and days[i + 1].year != days[i].year
                review_day = date(days[i].year, 12, 31)
            if i > s and due:
                apply(rules.evaluate(funds=funds, positions=positions, cash=cash,
                                     quotes=quotes(i), as_of=review_day))
            values.append(float(cash + sum(positions[r] * D(str(round(data.prices[r][i], 6))) for r in roles)))
        return {"values": values, "flows": flows, "trades": trades}

    def run(self, data, key, amount, monthly, fill="close"):
        s = start_index(data, key)
        if s is None:
            return None
        raw = {
            "strategy": self._strategy(data, s, amount, monthly, fill),
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
        out = {
            "start": start, "end": data.days[-1], "dates": data.days[s:],
            "series": {n: (_summary(data.days, s, v) if v else None) for n, v in raw.items()},
            "simulated_notes": notes,
        }
        if "resets" in raw["strategy"]:
            # Rules with resets (3Sig) assume each is confirmed; the page shows the count.
            out["assumed_resets"] = raw["strategy"]["resets"]
            out["reset_mix"] = raw["strategy"].get("reset_mix", "")
        return out

    def how_it_works(self, data):
        weights = ", ".join("%s%% %s" % ((w * 100).normalize(), self.sources[r].fund_label)
                            for r, w in self.rules.WEIGHTS)
        period = {"monthly": "month", "quarterly": "quarter"}.get(self.review_word, "year")
        rule = self.rule_text or (
            "sell what's above its weight, buy what's below, never borrowing.")
        money = {"GBP": "pounds", "USD": "US dollars", "EUR": "euros"}[self.CURRENCY]
        fx = ("" if self.CURRENCY == "USD" else
              " US-dollar series are converted with daily %s/USD rates." % self.CURRENCY)
        return (
            "Starts at the published weights (%s) and runs the live %s rule after each %s end: %s"
            % (weights, self.review_word, period, rule),
            "Monthly top-ups arrive on the first trading day of each month and buy "
            "underweight funds without selling existing holdings. Normal %s reviews "
            "still do the full rebalance; SPY and VTI invest immediately too." % self.review_word,
            "All values are in %s with dividends reinvested.%s Trades fill at that day's close with "
            "no dealing costs or tax." % (money, fx),
            "Before a fund existed, its role follows a stand-in, flagged on the page.",
            "A price is never carried forward more than a week. Days without a fresh price for every fund "
            "are left out, so the study ends on the last day all its prices are fresh.",
        )
