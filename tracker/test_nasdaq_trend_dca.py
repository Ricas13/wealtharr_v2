"""Regression and fidelity tests for Reddit's fixed Nasdaq trend strategy."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from tracker.strategies.nasdaq_trend_dca.v1 import (
    DCA_FRACTION, DCA_INSTALMENTS, Close, State, _months_completed, decide,
)


def series(spy=100, qqq=100, last=None):
    start = date(2025, 1, 1)
    return tuple(
        Close(start + timedelta(days=i),
              Decimal(str(spy if i == 199 else 100)),
              Decimal(str(qqq if i == 199 else 100)))
        for i in range(200)
    )


class NasdaqTrendDcaTests(SimpleTestCase):
    def test_short_history_fails_closed(self):
        self.assertEqual(decide(series(110)[:199], State()).status, "cannot_compute")

    def test_200_day_average_includes_signal_day(self):
        decision = decide(series(110), State())
        self.assertEqual(decision.target, "LEVERAGED")
        self.assertGreater(decision.spy_ratio, Decimal("1.04"))
        self.assertEqual(decision.leveraged_weight, Decimal("1"))

    def test_strict_thresholds_and_hysteresis(self):
        self.assertEqual(decide(series(100), State()).status, "cannot_compute")
        self.assertEqual(decide(series(100), State("ON")).target, "LEVERAGED")
        self.assertEqual(decide(series(100), State("OFF", date(2024, 1, 1))).target, "DCA")
        self.assertEqual(decide(series(90), State("ON")).target, "DCA")

    def test_exit_starts_in_cash_without_fake_qqq_allocation(self):
        decision = decide(series(90), State("ON"))
        self.assertEqual(decision.target, "DCA")
        self.assertEqual(decision.state.regime, "OFF")
        self.assertEqual(decision.state.off_since, series(90)[-1].day)
        self.assertIsNone(decision.qqq_weight)  # Never rebalance changing market value
        self.assertIsNone(decision.cash_weight)
        self.assertEqual(decision.dca_installments_due, 0)
        self.assertEqual(decision.dca_fraction_per_installment, DCA_FRACTION)

    def test_anniversary_schedule_including_month_end(self):
        self.assertEqual(_months_completed(date(2025, 1, 31), date(2025, 2, 27)), 0)
        self.assertEqual(_months_completed(date(2025, 1, 31), date(2025, 2, 28)), 1)
        self.assertEqual(_months_completed(date(2024, 1, 31), date(2024, 2, 29)), 1)
        self.assertEqual(_months_completed(date(2025, 1, 31), date(2025, 3, 30)), 1)
        self.assertEqual(_months_completed(date(2025, 1, 15), date(2025, 7, 15)), 6)
        self.assertEqual(_months_completed(date(2025, 1, 15), date(2026, 3, 15)), 12)

    def test_instalments_count_only_confirmed_purchases(self):
        initial = date(2025, 1, 1)
        end = series(100)[-1].day
        elapsed = _months_completed(initial, end)
        decision = decide(series(100), State("OFF", initial),
                          filled_dca_installments=2)
        self.assertEqual(decision.dca_installments_due, elapsed - 2)
        self.assertEqual(decision.dca_fraction_per_installment, Decimal("1") / 12)
        self.assertEqual(DCA_INSTALMENTS, 12)

    def test_overconfirmed_instalments_fail_closed(self):
        result = decide(series(100), State("OFF", date(2025, 7, 18)),
                        filled_dca_installments=2)
        self.assertEqual(result.status, "cannot_compute")
        result = decide(series(100), State("OFF", date(2024, 1, 1)),
                        filled_dca_installments=13)
        self.assertEqual(result.status, "cannot_compute")

    def test_qqq_plus_30_guard_latches_even_after_cooling(self):
        first = decide(series(110, 135), State())
        self.assertEqual(first.target, "UNLEVERAGED")
        self.assertEqual(first.state.guard, "UNLEVERAGED")
        cooled = decide(series(110), first.state)
        self.assertEqual(cooled.target, "UNLEVERAGED")
        self.assertEqual(cooled.state.guard, "UNLEVERAGED")

    def test_plus_40_guard_escalates_and_stays_in_cash(self):
        thirty = decide(series(110, 135), State())
        forty = decide(series(110, 145), thirty.state)
        self.assertEqual(forty.target, "CASH")
        self.assertEqual(forty.state.guard, "CASH")
        cooled = decide(series(110, 100), forty.state)
        self.assertEqual(cooled.target, "CASH")

    def test_guard_clears_only_after_spy_exit_then_new_entry(self):
        triggered = decide(series(110, 145), State())
        middle = decide(series(100), triggered.state)
        self.assertEqual(middle.target, "CASH")
        off = decide(series(90), middle.state)
        self.assertEqual(off.target, "DCA")
        self.assertEqual(off.state.guard, "NONE")
        again = decide(series(110), off.state)
        self.assertEqual(again.target, "LEVERAGED")
        self.assertEqual(again.state.guard, "NONE")

    def test_reentry_interrupts_dca_immediately(self):
        off = State("OFF", date(2025, 1, 1))
        entered = decide(series(110), off, filled_dca_installments=4)
        self.assertEqual(entered.target, "LEVERAGED")
        self.assertIsNone(entered.state.off_since)
        self.assertEqual(entered.dca_installments_due, 0)

    def test_nonfinite_prices_and_unsorted_dates_refuse(self):
        self.assertEqual(decide(series(0), State()).status, "cannot_compute")
        self.assertEqual(decide(series(110, "NaN"), State()).status, "cannot_compute")
        self.assertEqual(decide(tuple(reversed(series(110))), State()).status, "cannot_compute")

    def test_corrupted_persisted_state_fails_closed(self):
        self.assertEqual(decide(series(110), State("OFF")).status, "cannot_compute")
        self.assertEqual(decide(series(110), State("ON", guard="WRONG")).status, "cannot_compute")
        self.assertEqual(decide(series(110), State("OFF", date(2025, 1, 1), "CASH")).status,
                         "cannot_compute")

    def test_historical_prices_do_not_look_ahead(self):
        before = series(110)
        later = before + (Close(date(2027, 1, 1), Decimal("1"), Decimal("1")),)
        self.assertEqual(decide(before, State()).target, "LEVERAGED")
        self.assertEqual(decide(later, State("ON")).target, "DCA")
