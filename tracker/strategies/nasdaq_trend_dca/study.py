"""Study simulation for Nasdaq Trend + DCA, using the same pure evaluator.

Hypothetical data only. No portfolio ledger, saved signal or notification is
written here. Signals are USD SPY/QQQ daily raw closes and use 200 prior
trading observations. Study fund prices are in the account currency.
"""
from datetime import timedelta
from decimal import Decimal as D

from tracker.models import StudyPrice
from tracker.study import _new_month, _buy_and_hold, _summary, start_index, fill_orders
from tracker.study_fixed import FixedWeightStudy
from tracker.strategies.nasdaq_trend_dca import v1


class TrendStudy(FixedWeightStudy):
    def __init__(self, *, title, sources, currency="GBP"):
        super().__init__(title=title, rules=v1, sources=sources, currency=currency,
                         review_word="daily")
        self.SERIES = (("strategy", title), ("spy", "SPY (S&P 500)"),
                       ("vti", "Nasdaq-100 buy & hold"))

    def load_data(self):
        data = super().load_data()
        if not data.ready:
            return data
        usd = {}
        for symbol in ("SPY", "QQQ"):
            usd[symbol] = {r.as_of: r.close
                           for r in StudyPrice.objects.filter(symbol=symbol).only(
                               "as_of", "close")}
        if not usd["SPY"] or not usd["QQQ"]:
            data.days = []
            return data
        # Compute all possible signals once using pre-period history; never
        # bootstrap an SMA from the chosen study window (look-ahead risk).
        from tracker.strategies.nasdaq_trend_dca.v1 import trend_series
        data.trend = trend_series(list(usd["SPY"].items()), list(usd["QQQ"].items()))
        data.vti = list(data.prices["unleveraged"])
        return data

    def _strategy(self, data, s, amount, monthly, fill="close"):
        days = data.days
        roles = v1.ROLES
        funds = {role: role for role in roles}
        positions = {role: D("0") for role in roles}
        cash, trades = D(str(amount)), 0
        values, flows = [], [(days[s], amount)]
        pending = []
        cycle, inflows, spent = None, [], D("0")

        def prices(i):
            return {r: D(str(data.prices[r][i])) for r in roles}

        def apply(orders, i):
            nonlocal cash, trades, spent
            if not orders:
                return 0
            before = dict(positions)
            cash, filled = fill_orders(
                [(t.role, t.side, t.quantity) for t in orders],
                positions, cash, prices(i))
            if cycle is not None:
                price = prices(i)
                sold = sum((max(D("0"), before[r] - positions[r]) * price[r]
                            for r in roles), D("0"))
                if sold:
                    inflows.append((days[i], sold))
                spent += max(D("0"), positions["unleveraged"] - before["unleveraged"]) \
                    * price["unleveraged"]
            trades += filled
            return filled

        for i in range(s, len(days)):
            if pending:
                apply(pending, i)
                pending = []
            if i > s and _new_month(days, i) and monthly:
                cash += D(str(monthly))
                flows.append((days[i], monthly))
                if cycle is not None:
                    inflows.append((days[i], D(str(monthly))))
            signal = data.trend.get(days[i])
            if signal is not None:
                if signal.regime == "off" and signal.exit_on != cycle:
                    cycle, spent = signal.exit_on, D("0")
                    inflows[:] = [(max(cycle, days[s]), cash)] if cash > 0 else []
                elif signal.regime == "on":
                    cycle, spent = None, D("0")
                    inflows[:] = []
                result = v1.evaluate(
                    funds=funds, positions=positions, cash=cash,
                    quotes={r: v1.Quote(r, prices(i)[r], days[i]) for r in roles},
                    signal=signal, inflows=tuple(inflows), dca_spent=spent)
                if result.status == "action_needed":
                    if fill == "next" and i < len(days) - 1:
                        pending = result.trades
                    else:
                        apply(result.trades, i)
            values.append(float(cash + sum(positions[r] * prices(i)[r] for r in roles)))
        return {"values": values, "flows": flows, "trades": trades}

    def how_it_works(self, data):
        currency = {"GBP": "pounds", "USD": "US dollars", "EUR": "euros"}[self.CURRENCY]
        return (
            "Each completed US session evaluates SPY and QQQ against their own 200-session "
            "moving averages. Enter 3x Nasdaq above +4% SPY; exit below -3%. "
            "While bullish, QQQ above +30% uses 1x Nasdaq and above +40% goes to cash.",
            "After exiting, the cash is invested into 1x Nasdaq in 12 monthly instalments, "
            "one-twelfth a month. New money added while out follows its own 12-month "
            "schedule. Once the QQQ guard has reduced the position it stays reduced until "
            "SPY has exited and re-entered. A new bull signal re-enters 3x.",
            "The simulation uses actual fund prices when available and explicitly noted "
            "stand-ins before their first available trading date. TQQQ differs from LQQ3.",
            "User top-ups, if specified, are new money and are invested according to the "
            "current trend position (or the instalment schedule while out).",
            "All results are in %s. Study uses dividend-adjusted fund closes. "
            "Close fills are optimistic; use the next-close setting to include the "
            "execution delay. No tax, spread or dealing costs are modelled." % currency,
        )
