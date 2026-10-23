"""Regression tests for fixed, versioned trend rules."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from tracker.strategies.nasdaq_trend_dca.v1 import (
    CASH_GUARD, ENTRY, EXIT, Close, State, _months_completed, decide,
)


def series(spy, qqq=100):
    start = date(2025, 1, 1)
    return tuple(Close(start + timedelta(days=i), Decimal(str(spy if i == 199 else 100)),
                       Decimal(str(qqq if i == 199 else 100))) for i in range(200))


class NasdaqTrendDcaTests(SimpleTestCase):
    def test_short_history_fails_closed(self):
        self.assertEqual(decide(series(110)[:199], State()).status, "cannot_compute")

    def test_entry_uses_current_close_and_average(self):
        decision = decide(series(110), State())
        self.assertEqual(decision.target, "LEVERAGED")
        self.assertEqual(decision.leveraged_weight, 1)

    def test_exit_starts_dca_in_cash(self):
        decision = decide(series(90), State("ON"))
        self.assertEqual(decision.target, "DCA")
        self.assertEqual(decision.qqq_weight, 0)
        self.assertEqual(decision.cash_weight, 1)

    def test_hysteresis_preserves_on_and_off(self):
        self.assertEqual(decide(series(100), State("ON")).target, "LEVERAGED")
        self.assertEqual(decide(series(100), State("OFF", date(2024, 1, 1))).target, "DCA")

    def test_monthly_dca_caps_at_twelve(self):
        self.assertEqual(_months_completed(date(2025, 1, 31), date(2025, 2, 28)), 0)
        self.assertEqual(_months_completed(date(2025, 1, 15), date(2025, 7, 15)), 6)
        self.assertEqual(_months_completed(date(2025, 1, 15), date(2026, 3, 15)), 12)

    def test_guard_thresholds(self):
        self.assertEqual(decide(series(110, 145), State()).target, "CASH")
        self.assertEqual(decide(series(110, 135), State()).target, "UNLEVERAGED")

    def test_does_not_use_future_history(self):
        initial = series(110)
        future = initial + (Close(date(2027, 1, 1), Decimal(1), Decimal(1)),)
        self.assertEqual(decide(initial, State()).target, "LEVERAGED")
        self.assertEqual(decide(future, State("ON")).target, "DCA")

    def test_invalid_prices_and_order_fail(self):
        self.assertEqual(decide(series(0), State()).status, "cannot_compute")
        reversed_rows = tuple(reversed(series(110)))
        self.assertEqual(decide(reversed_rows, State()).status, "cannot_compute")
