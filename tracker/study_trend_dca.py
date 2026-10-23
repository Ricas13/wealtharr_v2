"""Historical Study replay for Nasdaq trend + DCA using real listed products.

StudyPrice is strictly hypothetical. SPY/QQQ signal thresholds use split-
adjusted Yahoo *close*, not dividend-reinvested adjusted-close; performance
uses the real ETF/ETP's dividend-adjusted closes. No invented pre-launch 3x
history: this Study starts when the actual leveraged instrument exists.
"""
from bisect import bisect_left
from datetime import timedelta
from decimal import Decimal

from .models import StudyPrice
from .study import _new_month
from .study_fixed import FixedWeightStudy, Source, _gbp_series, _last
from .strategies.nasdaq_trend_dca import v1

D = Decimal


class TrendDcaStudy(FixedWeightStudy):
    def __init__(self, *, currency, leveraged, unleveraged):
        super().__init__(
            title="3× Nasdaq Trend + DCA", rules=v1, currency=currency,
            review_word="daily", sources={
                "leveraged": Source(leveraged, "3× Nasdaq (" + leveraged + ")"),
                "unleveraged": Source(unleveraged, "Nasdaq (" + unleveraged + ")"),
            },
        )
        self.SERIES = (
            ("strategy", "3× Nasdaq Trend + DCA"),
            ("spy", "SPY buy and hold"),
            ("vti", "QQQ buy and hold"),
        )

    @property
    def NEEDS(self):
        return ", ".join((self.sources["leveraged"].fund, self.sources["unleveraged"].fund,
                          "SPY", "QQQ", "GBPUSD=X" if self.CURRENCY == "GBP" else ""))

    def load_data(self):
        data = super().load_data()
        if not data.ready:
            return data
        # Never show fake, constant or backfilled leveraged ETF history.
        starts = [data.real_from.get(role) for role in ("leveraged", "unleveraged")]
        if any(day is None for day in starts):
            data.days = []
            return data
        start = max(starts)
        idx = next((i for i, day in enumerate(data.days) if day >= start), len(data.days))
        data.days = data.days[idx:]
        data.spy = data.spy[idx:]
        data.vti = data.vti[idx:]
        data.prices = {key: values[idx:] for key, values in data.prices.items()}
        if not data.ready:
            return data
        raw = StudyPrice.objects.filter(symbol__in=("SPY", "QQQ"), currency="USD").order_by("as_of")
        paired = {}
        for observation in raw:
            if observation.close > 0:
                paired.setdefault(observation.as_of, {})[observation.symbol] = observation.close
        data.signal_history = tuple(
            v1.Close(day, values["SPY"], values["QQQ"])
            for day, values in sorted(paired.items()) if set(values) == {"SPY", "QQQ"}
        )
        if len(data.signal_history) < 200:
            data.days = []
            return data
        # Replace the second generic benchmark (VTI) with QQQ.
        target = self.CURRENCY
        if target != "USD":
            fx = {r.as_of: float(r.adj_close) for r in StudyPrice.objects.filter(symbol=target + "USD=X")}
            fx_days = sorted(fx)
        else:
            fx, fx_days = {}, []
        qqq = _gbp_series("QQQ", fx_days, fx, target=target)
        qqq_days = sorted(qqq)
        data.vti = [_last(qqq_days, qqq, d) for d in data.days]
        return data

    def _strategy(self, data, s, amount, monthly, fill="close"):
        days = data.days
        signal_history = data.signal_history
        prices = data.prices
        shares = {"leveraged": D(0), "unleveraged": D(0)}
        cash = D(str(amount))
        pending = None
        strategy = v1.State()
        reserve = D(0)
        filled = 0
        trades = 0
        values = []
        flows = [(days[s], amount)]
        signal_index = {point.day: i for i, point in enumerate(signal_history)}

        def price(role, i):
            return D(str(prices[role][i]))

        def execute(kind, i):
            nonlocal cash, reserve, filled, trades
            if kind in ("LEVERAGED", "UNLEVERAGED", "CASH", "DCA"):
                # Sells precede any buys; the USD close that triggers the
                # trade cannot be used as a contemporaneous UK execution.
                wanted = {"LEVERAGED": "leveraged", "UNLEVERAGED": "unleveraged"}.get(kind)
                for role in shares:
                    if shares[role] and role != wanted and kind != "DCA":
                        cash += shares[role] * price(role, i)
                        shares[role] = D(0)
                        trades += 1
                if kind == "DCA":
                    if shares["leveraged"]:
                        cash += shares["leveraged"] * price("leveraged", i)
                        shares["leveraged"] = D(0)
                        trades += 1
                    if shares["unleveraged"]:
                        cash += shares["unleveraged"] * price("unleveraged", i)
                        shares["unleveraged"] = D(0)
                        trades += 1
                    reserve, filled = cash, 0
                elif wanted and cash:
                    shares[wanted] += cash / price(wanted, i)
                    cash = D(0)
                    trades += 1

        # Feed the state machine all preceding US sessions, not just the
        # selected chart window. No historical holdings are fabricated.
        for point in signal_history[199:]:
            if point.day >= days[s]:
                break
            i = signal_index[point.day]
            outcome = v1.decide(signal_history[:i+1], strategy)
            if outcome.status == "ok":
                strategy = outcome.state

        last_target = None
        for i in range(s, len(days)):
            # Pending signal fills on the NEXT eligible study day. Even when
            # the UI selects "same close", US signals can only be known after
            # London trading ended.
            if pending:
                execute(pending, i)
                pending = None
            if i > s and _new_month(days, i) and monthly:
                cash += D(str(monthly))
                flows.append((days[i], monthly))
            j = signal_index.get(days[i])
            if j is not None and j >= 199 and i + 1 < len(days):
                outcome = v1.decide(signal_history[:j+1], strategy,
                                    filled_dca_installments=filled if strategy.regime == "OFF" else 0)
                if outcome.status == "ok":
                    strategy = outcome.state
                    target = outcome.target
                    if target == "DCA":
                        if last_target != "DCA":
                            pending = "DCA"
                        elif outcome.dca_installments_due and reserve > 0:
                            # Monthly purchases are based on the ORIGINAL
                            # proceeds, not rebalanced fractions of NAV.
                            to_spend = min(cash, reserve / D(12) * outcome.dca_installments_due)
                            if to_spend > 0:
                                shares["unleveraged"] += to_spend / price("unleveraged", i+1)
                                cash -= to_spend
                                filled += outcome.dca_installments_due
                                trades += 1
                    elif target != last_target:
                        pending = target
                    last_target = target
            values.append(float(cash + sum(shares[role] * price(role, i) for role in shares)))
        return {"values": values, "flows": flows, "trades": trades}

    def how_it_works(self, data):
        return (
            "SPY/QQQ 200-session close-to-SMA signals: SPY >+4% enter leveraged Nasdaq; "
            "SPY <-3% exit; QQQ >=+30% drop to unleveraged Nasdaq, >=+40% hold cash.",
            "QQQ safeguards remain active until the next full SPY sell/re-entry cycle. "
            "After exits, twelve monthly purchases spend the fixed original cash reserve.",
            "Hypothetical strategy switches use the next available closing prices. "
            "The first trading session cannot execute an order based on the same day's US close.",
            "Only periods with observed leveraged product prices are included; no synthetic "
            "TQQQ/LQQ3 returns are presented as real historical prices.",
            "The study uses dividend-adjusted holdings prices and separate unadjusted SPY/QQQ "
            "closes for signals. No tax, slippage or broker-specific dealing cost is modelled.",
            "LQQ3 and EQQQ are different products from TQQQ and QQQ; they may materially "
            "diverge because of financing, dividends, FX, fees and trading hours.",
        )
