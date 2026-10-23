"""Nasdaq Trend + DCA: signal hysteresis, guards, monthly proceeds and catalogue."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import SimpleTestCase

from tracker.strategies.fixed_weights import Quote
from tracker.strategies.nasdaq_trend_dca import v1
from tracker.strategies.registry import describe_strategy, module_for_key
from tracker.core.catalogue import selection_available, onboarding_manifest


def weekdays(count, first=date(2023, 1, 2)):
    days = []
    d = first
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


class NasdaqTrendRuleTests(SimpleTestCase):
    def test_no_history_or_undecided_band_refuses_to_guess(self):
        days = weekdays(200)
        flat = [(d, 100) for d in days]
        self.assertIsNone(v1.trend_at(flat[:199], flat[:199], days[-1]))
        self.assertIsNone(v1.trend_at(flat, flat, days[-1]))

    def test_hysteresis_and_qqq_guard_precedence(self):
        days = weekdays(240)
        spy = [(d, 100 if i < 200 else 110) for i, d in enumerate(days)]
        qqq = [(d, 100 if i < 200 else 150) for i, d in enumerate(days)]
        signals = v1.trend_series(spy, qqq)
        self.assertEqual(signals[days[201]].mode, "cash")
        # QQQ rising only 35% above its average reduces to unleveraged.
        qqq = [(d, 100 if i < 200 else 135) for i, d in enumerate(days)]
        signals = v1.trend_series(spy, qqq)
        self.assertEqual(signals[days[201]].mode, "unleveraged")
        # Return to the middle of the SPY band: stay in the earlier regime.
        spy[-1] = (days[-1], 101)
        self.assertEqual(v1.trend_at(spy, qqq, days[-1]).regime, "on")

    def test_bear_exit_starts_dca_after_first_month(self):
        days = weekdays(260)
        spy = [(d, 100 if i < 200 else 80) for i, d in enumerate(days)]
        qqq = [(d, 100) for d in days]
        state = v1.trend_at(spy, qqq, days[-1])
        self.assertEqual(state.mode, "dca")
        self.assertGreaterEqual(state.months_elapsed, 1)
        self.assertEqual(state.exit_on, days[200])

    def test_end_of_month_anniversary(self):
        self.assertEqual(v1.elapsed_months(date(2026, 1, 31), date(2026, 2, 28)), 1)

    def test_proceeds_dca_and_reentry_are_manual_actions(self):
        day = date(2026, 9, 30)
        funds = {"leveraged": "LQQ3.L", "unleveraged": "EQQQ.L"}
        quotes = {s: Quote(s, D("10"), day) for s in funds.values()}
        off = v1.TrendSignal(day, "dca", "off", date(2026, 8, 20),
                             1, D("0.9"), D("1.0"))
        result = v1.evaluate(funds=funds, positions={"LQQ3.L": D("12")},
                             cash=D("120"), quotes=quotes, signal=off)
        self.assertEqual([t.side for t in result.trades], ["SELL"])
        self.assertEqual(result.trades[0].symbol, "LQQ3.L")
        # The next confirmed review spends exactly 1/12 of cash proceeds.
        dca = v1.evaluate(funds=funds, positions={}, cash=D("240"),
                          quotes=quotes, signal=off)
        self.assertEqual([t.side for t in dca.trades], ["BUY"])
        self.assertEqual(dca.trades[0].symbol, "EQQQ.L")
        self.assertEqual(dca.trades[0].estimated_gbp, D("20.00"))
        # The same monthly purchase, once confirmed, must not be repeated.
        hold = v1.evaluate(funds=funds, positions={"EQQQ.L": D("2")},
                           cash=D("220"), quotes=quotes, signal=off, completed_dca=1)
        self.assertEqual(hold.trades, ())
        bull = v1.TrendSignal(day, "leveraged", "on", None, 0,
                              D("1.1"), D("1.02"))
        back = v1.evaluate(funds=funds, positions={"EQQQ.L": D("2")},
                           cash=D("220"), quotes=quotes, signal=bull)
        self.assertEqual([t.side for t in back.trades], ["SELL", "BUY"])
        self.assertEqual(back.trades[-1].symbol, "LQQ3.L")

    def test_negative_cash_and_unknown_holding_fail_closed(self):
        funds = {"leveraged": "LQQ3.L", "unleveraged": "EQQQ.L"}
        day = date(2026, 9, 30)
        quotes = {s: Quote(s, D("10"), day) for s in funds.values()}
        sig = v1.TrendSignal(day, "leveraged", "on", None, 0,
                             D("1.1"), D("1.0"))
        for positions, cash in [({}, D("-1")), ({"ZZZ": D("1")}, D("100"))]:
            result = v1.evaluate(funds=funds, positions=positions,
                                 cash=cash, quotes=quotes, signal=sig)
            self.assertEqual(result.status, "cannot_compute")
            self.assertEqual(result.trades, ())

    def test_released_alongside_ninesig_with_real_uk_lines(self):
        self.assertIsNotNone(describe_strategy("nasdaq-quarterly-target"))
        self.assertIsNotNone(module_for_key("nasdaq-trend-dca"))
        self.assertTrue(selection_available("nasdaq-trend-dca", "TRADING212", "ISA"))
        manifest = onboarding_manifest("nasdaq-trend-dca", "TRADING212", "ISA")
        self.assertEqual(manifest["roles"]["leveraged"]["yahoo_ticker"], "LQQ3.L")
        self.assertEqual(manifest["roles"]["unleveraged"]["yahoo_ticker"], "EQQQ.L")
        self.assertEqual(manifest["roles"]["unleveraged"]["raw_quote_unit"], "GBX")


class NasdaqTrendStudyTests(SimpleTestCase):
    def test_study_uses_the_daily_rules_and_owns_no_ledger(self):
        from tracker.study_fixed import FixedData
        from tracker.strategies.nasdaq_trend_dca.study import TrendStudy
        days = weekdays(270)
        data = FixedData(
            days=days,
            prices={"leveraged": [10.0 + 0.01 * i for i in range(270)],
                    "unleveraged": [10.0 + 0.005 * i for i in range(270)]},
            spy=[10.0] * 270, vti=[10.0] * 270)
        data.trend = {}
        for i in range(200, 270):
            if i < 225:
                data.trend[days[i]] = v1.TrendSignal(
                    days[i], "leveraged", "on", None, 0, D("1.1"), D("1.01"))
            else:
                exit_on = days[225]
                data.trend[days[i]] = v1.TrendSignal(
                    days[i], "dca", "off", exit_on,
                    v1.elapsed_months(exit_on, days[i]), D("0.90"), D("0.95"))
        engine = TrendStudy(title="Nasdaq trend", sources={})
        same = engine.run(data, "max", 1000, 0, "close")
        next_day = engine.run(data, "max", 1000, 0, "next")
        self.assertIsNotNone(same["series"]["strategy"])
        self.assertIsNotNone(next_day["series"]["strategy"])
        self.assertGreaterEqual(same["series"]["strategy"]["trades"], 2)
        self.assertEqual(same["series"]["strategy"]["put_in"], 1000)
        self.assertEqual(same["start"], days[0])
