"""Documented 9Sig decision-parity edge cases, independently computed."""
from datetime import date
from decimal import Decimal as D
from django.test import SimpleTestCase

from tracker.market_signals import (
    _drawdown_from_spy, _quarterly_closes,
    advice_for_holdings, signal_line_value, quarter_number,
)


def quarterly(**changes):
    params = {
        "start_date": date(2026, 1, 9),
        "as_of": date(2026, 6, 30),
        "currency": "GBP",
        "equity_symbol": "LQQ3.L", "safe_symbol": "ERNS.L",
        "equity_qty": D("1"), "equity_price": D("100"),
        "safe_qty": D("10"), "safe_price": D("100"),
        "cash": D("0"), "starting_equity_cost": D("600"),
        "reference": [(date(2026, 3, 31), D("80")),
                      (date(2026, 6, 30), D("85"))],
        "spy_reference": [(date(2026, 3, 31), D("100")),
                          (date(2026, 6, 30), D("100"))],
    }
    params.update(changes)
    return advice_for_holdings(**params)


def action_sum(signals, side, ticker):
    q = next(x for x in signals if x["kind"] == "QUARTERLY")
    return sum((D(a["estimated_gbp"]) for a in q["actions"]
                if a["side"] == side and a["symbol"] == ticker), D("0"))


class WorkbookAndKellyParity(SimpleTestCase):
    def test_independently_calculated_three_quarter_workbook_demo(self):
        # v1.07 DEMO 2010++: AV3=60000, AV4=65400,
        # AV5=71286, AV6=80701.74 after 6000 additional cash.
        start = date(2010, 2, 11)
        self.assertEqual(signal_line_value(
            start_date=start, as_of=date(2010, 3, 31),
            starting_equity_cost=D("60000")), D("65400.00"))
        self.assertEqual(signal_line_value(
            start_date=start, as_of=date(2010, 6, 30),
            starting_equity_cost=D("60000")), D("71286.00"))
        self.assertEqual(signal_line_value(
            start_date=start, as_of=date(2010, 9, 30),
            starting_equity_cost=D("60000"),
            contributions=[(date(2010, 8, 15), D("6000"))]), D("80701.74"))

    def test_spy_30down_uses_previous_eight_quarter_peak_even_during_recovery(self):
        ref = _quarterly_closes(
            [(date(2026, 3, 31), D("100")),
             (date(2026, 6, 30), D("60")),
             (date(2026, 9, 30), D("65"))],
            date(2026, 9, 30))
        signal, decline = _drawdown_from_spy(ref, quarter_number(date(2026, 9, 30)))
        self.assertTrue(signal)
        self.assertEqual(decline, D("0.35"))
        result = quarterly(as_of=date(2026, 9, 30),
                           spy_reference=[(date(2026, 3, 31), D("100")),
                                          (date(2026, 6, 30), D("60")),
                                          (date(2026, 9, 30), D("65"))],
                           equity_price=D("1200"))
        self.assertTrue(any(s["kind"] == "THIRTY_DOWN" for s in result))
        self.assertEqual(next(s for s in result if s["kind"] == "QUARTERLY")["actions"][0]["side"], "HOLD")

    def test_30down_does_not_fire_when_only_tqqq_crashes(self):
        signals = quarterly(
            reference=[(date(2026, 3, 31), D("100")),
                       (date(2026, 6, 30), D("35"))],
            spy_reference=[(date(2026, 3, 31), D("100")),
                           (date(2026, 6, 30), D("91"))])
        self.assertFalse(any(s["kind"] == "THIRTY_DOWN" for s in signals))

    def test_small_buy_funded_in_full_below_90_percent_reserve(self):
        # 600 * (1.09^2) = 712.86; equity worth 100, reserves 1000.
        # Target shortfall 612.86 is below 90% reserve: no throttle.
        signals = quarterly()
        self.assertEqual(action_sum(signals, "BUY", "LQQ3.L"), D("612.86"))
        self.assertEqual(action_sum(signals, "SELL", "ERNS.L"), D("612.86"))

    def test_big_buy_uses_workbook_81_percent_reserve_throttle(self):
        # 800 * 1.09^2 = 950.48; 1 GBP equity -> 949.48 shortfall.
        # GBP reserve = 500 cash + 500 ERNS => 1000.
        # Spreadsheet BF uses 0.9 * (1 - 0.1) = 0.81 for a large buy.
        signals = quarterly(
            equity_price=D("1"), starting_equity_cost=D("800"),
            safe_qty=D("5"), cash=D("500"))
        self.assertEqual(action_sum(signals, "SELL", "ERNS.L"), D("310.00"))
        self.assertEqual(action_sum(signals, "BUY", "LQQ3.L"), D("810.00"))
        self.assertIn("large-buy throttle", next(
            s for s in signals if s["kind"] == "QUARTERLY")["message"])

    def test_small_rebalance_is_not_ignored_by_invented_half_percent_deadband(self):
        # 100 * 1.09^2 = 118.81; equity GBP 114, shortfall GBP 4.81.
        signals = quarterly(
            starting_equity_cost=D("100"), equity_price=D("114"))
        self.assertGreater(action_sum(signals, "BUY", "LQQ3.L"), D("4"))

    def test_30down_exception_takes_precedence_over_large_cash_shortage(self):
        signals = quarterly(
            equity_price=D("10"), safe_qty=D("0.1"),
            spy_reference=[(date(2026, 3, 31), D("100")),
                           (date(2026, 6, 30), D("60"))])
        q = next(s for s in signals if s["kind"] == "QUARTERLY")
        self.assertEqual([x["side"] for x in q["actions"]], ["HOLD"])

    def test_100_percent_bond_deficit_is_reset_outside_30down(self):
        signals = quarterly(equity_price=D("10"), safe_qty=D("0.1"))
        q = next(s for s in signals if s["kind"] == "QUARTERLY")
        self.assertIn("reset to 60%", q["message"])
