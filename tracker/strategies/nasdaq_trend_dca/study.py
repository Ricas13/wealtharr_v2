"""Study simulation for Nasdaq Trend + DCA, using the same pure evaluator.

Hypothetical data only. No portfolio ledger, saved signal or notification is
written here. Signals are USD SPY/QQQ daily adjusted closes and use 200 prior
trading observations. Study fund prices are in the account currency.
"""
from datetime import timedelta
from decimal import Decimal as D

from tracker.models import StudyPrice
from tracker.study import _new_month, _buy_and_hold, _summary, start_index, fill_orders
from tracker.study_fixed import FixedWeightStudy, _gbp_series, _last
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
            usd[symbol] = {r.as_of: r.adj_close
                           for r in StudyPrice.objects.filter(symbol=symbol).only(
                               "as_of", "adj_close")}
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
        pending, completed, exit_on, completed_month = [], 0, None, None

        def prices(i):
            return {r: D(str(data.prices[r][i])) for r in roles}

        def apply(orders, i):
            nonlocal cash, trades
            if not orders:
                return 0
            cash, filled = fill_orders(
                [(t.role, t.side, t.quantity) for t in orders],
                positions, cash, prices(i))
            trades += filled
            return filled

        for i in range(s, len(days)):
            if pending:
                orders, was_dca, month = pending
                if apply(orders, i) and was_dca:
                    completed += 1
                    completed_month = month
                pending = []
            if i > s and _new_month(days, i) and monthly:
                cash += D(str(monthly))
                flows.append((days[i], monthly))
            signal = data.trend.get(days[i])
            if signal is not None:
                if signal.regime == "off" and signal.exit_on != exit_on:
                    exit_on, completed, completed_month = signal.exit_on, 0, None
                elif signal.regime == "on":
                    exit_on, completed, completed_month = None, 0, None
                month = days[i].strftime("%Y-%m")
                count = (max(completed, signal.months_elapsed)
                         if signal.mode == "dca" and completed_month == month
                         else completed)
                result = v1.evaluate(
                    funds=funds, positions=positions, cash=cash,
                    quotes={r: v1.Quote(r, prices(i)[r], days[i]) for r in roles},
                    signal=signal, completed_dca=count)
                if result.status == "action_needed":
                    is_dca = signal.mode == "dca" and any(
                        t.side == "BUY" and t.role == "unleveraged" for t in result.trades)
                    if fill == "next" and i > s:
                        pending = (result.trades, is_dca, month)
                    elif apply(result.trades, i) and is_dca:
                        completed += 1
                        completed_month = month
            values.append(float(cash + sum(positions[r] * prices(i)[r] for r in roles)))
        return {"values": values, "flows": flows, "trades": trades}

    def how_it_works(self, data):
        currency = {"GBP": "pounds", "USD": "US dollars", "EUR": "euros"}[self.CURRENCY]
        return (
            "Each completed US session evaluates SPY and QQQ against their own 200-session "
            "moving averages. Enter 3x Nasdaq above +4% SPY; exit below -3%. "
            "While bullish, QQQ above +30% uses 1x Nasdaq and above +40% goes to cash.",
            "After exiting, cash is invested into 1x Nasdaq in 12 monthly instalments "
            "(one-twelfth of exit proceeds per month). A new bull signal re-enters 3x.",
            "The simulation uses actual fund prices when available and explicitly noted "
            "stand-ins before their first available trading date. TQQQ differs from LQQ3.",
            "User top-ups, if specified, are separate new money invested according to "
            "the current strategy choice. DCA purchases are not added contributions.",
            "All results are in %s. Study uses dividend-adjusted fund closes. "
            "Close fills are optimistic; use the next-close setting to include the "
            "execution delay. No tax, spread or dealing costs are modelled." % currency,
        )
