"""Workbook-derived quarterly target and 9Sig safety rules, with edge cases."""
from datetime import date
from decimal import Decimal as D
from django.test import SimpleTestCase

from tracker.market_signals import advice_for_holdings, signal_line_value


def advise(**changes):
    args = dict(
        start_date=date(2026, 1, 10), as_of=date(2026, 6, 30),
        currency="GBP", equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
        equity_qty=D("10"), equity_price=D("70"),
        safe_qty=D("4"), safe_price=D("100"),
        cash=D("100"), starting_equity_cost=D("600"),
        reference=[(date(2026, 3, 31), D("80")),
                   (date(2026, 6, 30), D("90"))],
        spy_reference=[(date(2026, 3, 31), D("100")),
                       (date(2026, 6, 30), D("97"))],
        contributions=(),
    )
    args.update(changes)
    return next(x for x in advice_for_holdings(**args) if x["kind"] == "QUARTERLY")


class WorkbookArithmeticTests(SimpleTestCase):
    def test_quarterly_9_percent_is_quarterly_not_annualized(self):
        self.assertEqual(signal_line_value(
            start_date=date(2026, 1, 10), as_of=date(2026, 3, 31),
            starting_equity_cost=D("600")), D("654.00"))
        self.assertEqual(signal_line_value(
            start_date=date(2026, 1, 10), as_of=date(2026, 6, 30),
            starting_equity_cost=D("600")), D("712.86"))

    def test_same_period_contribution_is_not_compounded_early(self):
        actual = signal_line_value(
            start_date=date(2026, 1, 10), as_of=date(2026, 6, 30),
            starting_equity_cost=D("600"),
            contributions=[(date(2026, 4, 15), D("500"))])
        self.assertEqual(actual, D("962.86"))  # 712.86 + 500/2, not 985.36

    def test_initial_quarter_later_deposit_gets_half_added(self):
        self.assertEqual(signal_line_value(
            start_date=date(2026, 1, 10), as_of=date(2026, 3, 31),
            starting_equity_cost=D("600"),
            contributions=[(date(2026, 2, 12), D("200"))]), D("754.00"))

    def test_earlier_contribution_compounds_only_in_subsequent_quarters(self):
        actual = signal_line_value(
            start_date=date(2026, 1, 10), as_of=date(2026, 9, 30),
            starting_equity_cost=D("600"),
            contributions=[(date(2026, 4, 15), D("500"))])
        self.assertEqual(actual, D("1049.52"))  # 600*1.09**3 + 250*1.09

    def test_future_cash_must_not_enter_completed_quarter(self):
        self.assertEqual(signal_line_value(
            start_date=date(2026, 1, 10), as_of=date(2026, 6, 30),
            starting_equity_cost=D("600"),
            contributions=[(date(2026, 7, 1), D("500"))]), D("712.86"))

    def test_no_artificial_reset_after_eight_quarters_without_sales(self):
        q = advise(no_sell_quarters=8)
        self.assertNotIn("reset to 60%", q["message"])

    def test_30_down_can_skip_two_sells_and_force_third_to_reset(self):
        spy = [(date(2026, 3, 31), D("100")),
               (date(2026, 6, 30), D("60"))]
        skip = advise(
            spy_reference=spy, equity_price=D("200"),
            prior_skipped_sells=1, prior_drawdown=True,
            down_age_quarters=2)
        self.assertEqual(skip["actions"][0]["side"], "HOLD")
        third = advise(
            spy_reference=spy, equity_price=D("200"),
            prior_skipped_sells=2, prior_drawdown=True,
            down_age_quarters=3)
        self.assertIn("Two sell signals", third["message"])
        self.assertTrue(any(a["side"] == "SELL" for a in third["actions"]))

    def test_expired_eight_quarter_30_down_requires_rebaseline(self):
        q = advise(prior_drawdown=True, down_age_quarters=8)
        self.assertIn("Eight-quarter 30 Down", q["message"])
        self.assertIn("reset to 60%", q["message"])

    def test_one_hundred_percent_single_quarter_tqqq_gain_triggers_reset(self):
        q = advise(reference=[(date(2026, 3, 31), D("50")),
                              (date(2026, 6, 30), D("100"))])
        self.assertIn("doubled", q["message"])
        self.assertIn("reset to 60%", q["message"])

    def test_requested_buy_more_than_entire_safe_sleeve_is_reset(self):
        q = advise(
            cash=D("0"), safe_qty=D("1"), equity_qty=D("1"),
            equity_price=D("20"), starting_equity_cost=D("600"))
        self.assertIn("more than 100% of safe assets", q["message"])

    def test_ninety_percent_safe_asset_throttle_never_sells_entire_sleeve(self):
        q = advise(equity_qty=D("1"), equity_price=D("100"),
                   safe_qty=D("10"), cash=D("0"), starting_equity_cost=D("600"))
        sold = sum(D(a["estimated_gbp"]) for a in q["actions"]
                   if a["side"] == "SELL" and a["symbol"] == "ERNS.L")
        self.assertLessEqual(sold, D("900"))

    def test_under_thirty_down_safe_asset_shortage_is_hold_not_forced_buy(self):
        q = advise(
            spy_reference=[(date(2026, 3, 31), D("100")),
                           (date(2026, 6, 30), D("50"))],
            cash=D("0"), safe_qty=D("1"), equity_qty=D("1"),
            equity_price=D("20"), starting_equity_cost=D("600"))
        self.assertEqual([a["side"] for a in q["actions"]], ["HOLD"])
