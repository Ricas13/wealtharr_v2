"""All Weather (UK) v1: pure yearly-rebalance rules."""
from datetime import date
from decimal import Decimal as D

from django.test import SimpleTestCase

from tracker.strategies.all_weather.v1 import Fund, Quote, WEIGHTS, evaluate

YEAR_END = date(2026, 12, 31)
SYMBOLS = {"stocks": "S.L", "long": "L.L", "inter": "I.L", "gold": "G.L", "comm": "C.L"}
FUNDS = tuple(Fund(role, SYMBOLS[role], weight) for role, weight in WEIGHTS)


def quotes(**prices):
    base = {"S.L": "100", "L.L": "10", "I.L": "10", "G.L": "50", "C.L": "5"}
    base.update({SYMBOLS[k]: v for k, v in prices.items()})
    return {s: Quote(s, D(p), YEAR_END) for s, p in base.items()}


def on_target(total=D("10000")):
    q = quotes()
    return {f.symbol: total * f.weight / q[f.symbol].price_gbp for f in FUNDS}


class AllWeatherRulesTests(SimpleTestCase):
    def test_weights_are_the_published_mix(self):
        self.assertEqual(dict(WEIGHTS), {"stocks": D("0.30"), "long": D("0.40"), "inter": D("0.15"),
                                         "gold": D("0.075"), "comm": D("0.075")})
        self.assertEqual(sum(w for _, w in WEIGHTS), D("1"))

    def test_on_target_needs_no_trades(self):
        result = evaluate(funds=FUNDS, positions=on_target(), cash=D("0"), quotes=quotes(), as_of=YEAR_END)
        self.assertEqual((result.status, result.trades), ("ok", ()))

    def test_rally_sells_the_winner_and_buys_the_rest_without_borrowing(self):
        # Shares double: £3,000 -> £6,000 of a £13,000 portfolio.
        result = evaluate(funds=FUNDS, positions=on_target(), cash=D("0"),
                          quotes=quotes(stocks="200"), as_of=YEAR_END)
        self.assertEqual(result.status, "action_needed")
        sides = {t.symbol: t.side for t in result.trades}
        self.assertEqual(sides["S.L"], "SELL")
        self.assertTrue(all(sides[s] == "BUY" for s in ("L.L", "I.L", "G.L", "C.L")))
        sold = sum(t.estimated_gbp for t in result.trades if t.side == "SELL")
        bought = sum(t.estimated_gbp for t in result.trades if t.side == "BUY")
        self.assertLessEqual(bought, sold)
        # Sells first, then buys; every action carries the price and date it used.
        self.assertEqual([t.side for t in result.trades][0], "SELL")
        action = result.trades[0].as_action()
        self.assertEqual((action["price_date"], action["role"]), ("2026-12-31", "stocks"))
        # After trading, shares are back to ~30% of the total.
        stocks_after = (on_target()["S.L"] - result.trades[0].quantity) * D("200")
        self.assertAlmostEqual(float(stocks_after / D("13000")), 0.30, places=3)

    def test_new_cash_is_split_at_the_weights(self):
        result = evaluate(funds=FUNDS, positions=on_target(), cash=D("2000"), quotes=quotes(), as_of=YEAR_END)
        buys = {t.role: t.estimated_gbp for t in result.trades}
        self.assertTrue(all(t.side == "BUY" for t in result.trades))
        self.assertAlmostEqual(float(buys["long"]), 800, delta=1)
        self.assertAlmostEqual(float(buys["gold"]), 150, delta=1)

    def test_small_drifts_are_ignored(self):
        positions = on_target()
        positions["S.L"] += D("0.1")  # £10 over
        result = evaluate(funds=FUNDS, positions=positions, cash=D("0"), quotes=quotes(), as_of=YEAR_END)
        self.assertEqual(result.trades, ())

    def test_cannot_compute_gives_zero_trades_and_one_reason(self):
        q = quotes()
        del q["G.L"]
        cases = (
            dict(positions=on_target(), cash=D("0"), quotes=q),
            dict(positions={**on_target(), "OTHER.L": D("1")}, cash=D("0"), quotes=quotes()),
            dict(positions=on_target(), cash=D("-1"), quotes=quotes()),
            dict(positions={}, cash=D("0"), quotes=quotes()),
        )
        for case in cases:
            with self.subTest(case=case):
                result = evaluate(funds=FUNDS, as_of=YEAR_END, **case)
                self.assertEqual((result.status, result.trades), ("cannot_compute", ()))
                self.assertTrue(result.reason)

    def test_never_sells_more_than_held(self):
        positions = on_target()
        result = evaluate(funds=FUNDS, positions=positions, cash=D("0"),
                          quotes=quotes(stocks="1000"), as_of=YEAR_END)
        sell = next(t for t in result.trades if t.side == "SELL")
        self.assertLessEqual(sell.quantity, positions["S.L"])
