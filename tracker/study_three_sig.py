"""Hypothetical 3Sig Study: replays the live 3Sig rules at each quarter end.

Same contract as the other studies: reads StudyPrice only, writes nothing,
never values real holdings or feeds instructions or alerts. Prices come from
the fixed-weight loader (stand-ins before a fund existed, flagged); 30 Down
reads SPY's US-dollar closes, as live reviews do. Every reset is assumed
confirmed and filled at the quarter close, and counted on the page.
"""
from bisect import bisect_left
from datetime import timedelta
from decimal import Decimal

from .models import StudyPrice
from .study import _floor_qty, _new_month, _quarter_end
from .study_fixed import FixedWeightStudy

D = Decimal


class ThreeSigStudy(FixedWeightStudy):
    def __init__(self, *, rules, sources, currency="GBP"):
        super().__init__(title="3Sig", rules=rules, sources=sources,
                         review_word="quarterly", currency=currency)

    def load_data(self):
        data = super().load_data()
        data.spy_usd = [(r.as_of, r.close) for r in
                        StudyPrice.objects.filter(symbol="SPY").order_by("as_of").only("as_of", "close")]
        return data

    def _strategy(self, data, s, amount, monthly):
        rules, days = self.rules, data.days
        growth_role, safe_role = list(self.sources)
        growth, safe = data.prices[growth_role], data.prices[safe_role]
        spy_days = [d for d, _ in data.spy_usd]

        def px(series, i):
            return D(str(round(series[i], 6)))

        g0, s0 = px(growth, s), px(safe, s)
        equity = _floor_qty(D(amount) * rules.STOCK_SHARE, g0)
        reserve = _floor_qty(D(amount) * (1 - rules.STOCK_SHARE), s0)
        cash = D(amount) - equity * g0 - reserve * s0
        start_day, start_cost = days[s], equity * g0
        contributions, down_q, skipped, trades, resets = [], None, 0, 0, 0
        flows, values = [(days[s], amount)], []
        for i in range(s, len(days)):
            day = days[i]
            g, sf = px(growth, i), px(safe, i)
            if i > s and _new_month(days, i) and monthly:
                cash += D(monthly)
                contributions.append((day, D(monthly)))
                flows.append((day, monthly))
            last_of_quarter = (i + 1 < len(days) and
                               rules.quarter_number(days[i + 1]) != rules.quarter_number(day))
            if i > s and last_of_quarter and equity > 0:
                qend = _quarter_end(day)
                review_q = rules.quarter_number(qend)
                k = bisect_left(spy_days, day + timedelta(days=1))
                alerts = rules.advice_for_holdings(
                    start_date=start_day, as_of=qend, currency=self.CURRENCY,
                    equity_symbol=growth_role, safe_symbol=safe_role,
                    equity_qty=equity, equity_price=g, safe_qty=reserve, safe_price=sf,
                    cash=cash, starting_equity_cost=start_cost,
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
                            continue
                        price = g if action["symbol"] == growth_role else sf
                        held = equity if action["symbol"] == growth_role else reserve
                        if action["side"] == "SELL":
                            qty = min(qty, held)
                        change = qty if action["side"] == "BUY" else -qty
                        if action["symbol"] == growth_role:
                            equity += change
                        else:
                            reserve += change
                        cash -= change * price
                        trades += 1
                    cash = max(cash, D("0"))
                    if plan.get("reset_reason"):
                        # Modelling assumption: the owner confirms the 80/20
                        # reset and it fills at the quarter close.
                        start_day, start_cost = qend, equity * g
                        contributions, down_q, skipped = [], None, 0
                        resets += 1
            values.append(float(equity * g + reserve * sf + cash))
        return {"values": values, "flows": flows, "trades": trades, "resets": resets,
                "reset_mix": "80/20"}

    def how_it_works(self, data):
        stock, bond = (src.fund_label for src in self.sources.values())
        money = {"GBP": "pounds", "USD": "US dollars", "EUR": "euros"}[self.CURRENCY]
        fx = ("" if self.CURRENCY == "USD" else
              " US-dollar series are converted with daily %s/USD rates." % self.CURRENCY)
        return (
            "3Sig starts 80%% stocks (%s) / 20%% bonds (%s) and runs the live quarterly rule at each "
            "quarter end: the 3%% signal line, half of new money, the 30%% bond ceiling, 30 Down (from SPY), "
            "the large-buy throttle and 80/20 resets." % (stock, bond),
            "Assumption: every 80/20 reset is treated as confirmed and filled at that quarter's close. In live "
            "tracking every reset waits for you to confirm it, so real results can differ. The number of "
            "resets in each run is shown with its results.",
            "Monthly top-ups arrive on the first trading day of each month and wait as cash for the next "
            "quarterly review; SPY and VTI invest them that day.",
            "All values are in %s with dividends reinvested.%s Trades fill at that day's close with no "
            "dealing costs or tax." % (money, fx),
            "Before a fund existed, its role follows a stand-in, flagged on the page.",
            "A price is never carried forward more than a week. Days without a fresh price for every fund "
            "are left out, so the study ends on the last day all its prices are fresh.",
        )
