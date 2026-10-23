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
        """Replay decisions chronologically and fill ONLY on the following bar.

        A deliberately deterministic hypothetical: no forced fills at signal
        close, no use of next-day execution prices to size yesterday's order,
        and no rebalancing of already-purchased DCA ETF shares.
        """
        days, prices = data.days, data.prices
        signals = data.signal_history
        shares = {"leveraged": D(0), "unleveraged": D(0)}
        cash = D(str(amount))
        reserve = D(0)
        filled = 0
        trades = 0
        pending = None
        flows = [(days[s], amount)]
        values = []
        state = v1.State()
        signal_index = {point.day: index for index, point in enumerate(signals)}

        def price(role, index):
            return D(str(prices[role][index]))

        def execute(order, index):
            nonlocal cash, reserve, filled, trades
            kind, count = order
            if kind == "INSTALLMENT":
                spent = min(cash, (reserve / D(12) * D(count)).quantize(D("0.01")))
                if spent > 0:
                    shares["unleveraged"] += spent / price("unleveraged", index)
                    cash -= spent
                    filled += count
                    trades += 1
                return
            wanted = {"LEVERAGED": "leveraged",
                      "UNLEVERAGED": "unleveraged"}.get(kind)
            for role in shares:
                if shares[role] and role != wanted:
                    cash += shares[role] * price(role, index)
                    shares[role] = D(0)
                    trades += 1
            if kind == "EXIT":
                reserve, filled = cash, 0
            elif wanted and cash > 0:
                shares[wanted] += cash / price(wanted, index)
                cash = D(0)
                trades += 1

        # Warm up the exact same pure state machine using historical sessions.
        for j in range(199, len(signals)):
            if signals[j].day >= days[s]:
                break
            decision = v1.decide(signals[:j+1], state)
            if decision.status == "ok":
                state = decision.state
        if state.regime == "OFF":
            # This study starts with actual hypothetical new capital today,
            # not fabricated old sales/DCA purchases.
            state = v1.State("OFF", days[s])

        last_target = None
        for i in range(s, len(days)):
            if pending:
                execute(pending, i)
                pending = None
            if i > s and _new_month(days, i) and monthly:
                cash += D(str(monthly))
                flows.append((days[i], monthly))
            j = signal_index.get(days[i])
            if j is not None and j >= 199 and i < len(days)-1:
                decision = v1.decide(
                    signals[:j+1], state,
                    filled_dca_installments=filled if state.regime == "OFF" else 0)
                if decision.status == "ok":
                    state = decision.state
                    target = decision.target
                    if target == "DCA":
                        if last_target != "DCA":
                            pending = ("EXIT", 0)
                        elif decision.dca_installments_due and reserve > 0:
                            pending = ("INSTALLMENT", decision.dca_installments_due)
                    elif target != last_target or (cash > 0 and monthly):
                        pending = (target, 0)
                    last_target = target
            values.append(float(cash + sum(
                shares[role] * price(role, i) for role in shares)))
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
