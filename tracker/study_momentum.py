"""Hypothetical Study for Dual Momentum: the monthly switch replayed over long history.

Same contract as the other studies: StudyPrice only, nothing written, never
used for real figures, instructions or alerts. Each month end calls the
strategy's own pure ``evaluate``. Stand-ins before a fund existed are flagged.
"""
from bisect import bisect_left
from datetime import date, timedelta
from decimal import Decimal

from .core.calendar import months_before
from .study import _new_month
from .study_fixed import FixedWeightStudy

D = Decimal


def _year_before(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


class MomentumStudy(FixedWeightStudy):
    """Sources must include a "cash" role: the signal-only cash series."""

    def __init__(self, *, title, rules, sources, currency="GBP"):
        super().__init__(title=title, rules=rules, sources=sources, review_word="monthly",
                         currency=currency)

    def _strategy(self, data, s, amount, monthly):
        rules, days = self.rules, data.days
        roles = rules.ROLES
        funds = {r: r for r in roles}

        def price(role, i):
            return D(str(round(data.prices[role][i], 6)))

        def at_or_before(day):
            i = bisect_left(days, day + timedelta(days=1)) - 1
            return i if i >= 0 and (day - days[i]).days <= rules.MAX_GAP_DAYS else None

        positions = {r: D("0") for r in roles}
        cash = D(str(amount))
        trades = 0

        def review(i, as_of, window_start):
            nonlocal cash, trades
            j = at_or_before(window_start)
            if j is None:
                return
            lookbacks = {k: rules.Lookback(rules.Quote(k, price(k, j), days[j]),
                                           rules.Quote(k, price(k, i), days[i]))
                         for k in ("us", "intl", "cash")}
            quotes = {r: rules.Quote(r, price(r, i), days[i]) for r in roles}
            result = rules.evaluate(funds=funds, positions=positions, cash=cash, quotes=quotes,
                                    lookbacks=lookbacks, as_of=as_of, window_start=window_start)
            for t in result.trades:
                if t.side == "BUY":
                    positions[t.role] += t.quantity
                    cash -= t.quantity * t.price_gbp
                else:
                    positions[t.role] -= t.quantity
                    cash += t.quantity * t.price_gbp
                trades += 1
            cash = max(cash, D("0"))

        review(s, days[s], _year_before(days[s]))
        flows, values = [(days[s], amount)], []
        for i in range(s, len(days)):
            if i > s and _new_month(days, i) and monthly:
                cash += D(str(monthly))
                flows.append((days[i], monthly))
            month_end = i + 1 < len(days) and days[i + 1].month != days[i].month
            if i > s and month_end:
                from calendar import monthrange
                as_of = date(days[i].year, days[i].month, monthrange(days[i].year, days[i].month)[1])
                review(i, as_of, months_before(as_of, rules.LOOKBACK_MONTHS))
            values.append(float(cash + sum(positions[r] * price(r, i) for r in roles)))
        return {"values": values, "flows": flows, "trades": trades}

    def how_it_works(self, data):
        money = {"GBP": "pounds", "USD": "US dollars", "EUR": "euros"}[self.CURRENCY]
        fx = ("" if self.CURRENCY == "USD" else
              " US-dollar series are converted with daily %s/USD rates (a hedged fund's stand-in uses "
              "its own returns)." % self.CURRENCY)
        return (
            "At each month end the live rule compares 12-month total returns: if US shares beat cash, "
            "hold whichever of US or world ex-US shares did better, otherwise hold bonds. 100% sits in "
            "the chosen fund; switches sell everything else.",
            "Monthly top-ups arrive on the first trading day of each month and wait as cash for the "
            "month-end review; SPY and VTI invest them that day.",
            "All values are in %s with dividends reinvested.%s Trades fill at that day's close with no "
            "dealing costs or tax." % (money, fx),
            "The first 12 months of data are only used to measure momentum. Before a fund existed, its "
            "role follows a stand-in, flagged on the page.",
            "A price is never carried forward more than a week. Days without a fresh price for every fund "
            "are left out, so the study ends on the last day all its prices are fresh.",
        )
