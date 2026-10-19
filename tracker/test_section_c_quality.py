"""Section C: fail-closed vendor quote sanity and no-lookahead regressions."""
import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from tracker.core.prices import check_price_series
from tracker.market_signals import fetch_chart, sync_ticker
from tracker.models import MarketObservation, YahooSync
from tracker.strategies.nine_sig.v1 import evaluate
from tracker.test_nine_sig_v1 import sample, quote


class PriceSanityContractTests(SimpleTestCase):
    def test_normal_movements_accepted(self):
        day = date(2026, 3, 31)
        accepted = check_price_series("ERNS.L", (
            (day - timedelta(days=1), D("100")),
            (day, D("100.09"))), as_of=day)
        self.assertEqual(accepted.status, "APPROVED")

    def test_stale_future_zero_and_absurd_moves_held(self):
        day = date(2026, 3, 31)
        cases = [
            ([(day - timedelta(days=9), D("100"))], "STALE_PRICE"),
            ([(day + timedelta(days=1), D("100"))], "BAD_PRICE"),
            ([(day, D("0"))], "BAD_PRICE"),
            ([(day - timedelta(days=1), D("100")),
              (day, D("10000"))], "LARGE_MOVE"),
            ([(day - timedelta(days=1), D("100")),
              (day - timedelta(days=1), D("101"))], "DUPLICATE_DATE"),
        ]
        for quotes, code in cases:
            with self.subTest(code=code):
                decision = check_price_series("LQQ3.L", quotes, as_of=day)
                self.assertEqual((decision.status, decision.code), ("HOLD", code))

    def test_legitimate_3x_moves_have_separate_safety_band(self):
        day = date(2026, 3, 31)
        series = ((day - timedelta(days=1), D("100")), (day, D("160")))
        self.assertEqual(check_price_series(
            "LQQ3.L", series, as_of=day).status, "APPROVED")
        self.assertEqual(check_price_series(
            "ERNS.L", series, as_of=day).code, "LARGE_MOVE")

    def test_instrument_version_and_quarter_end_reference_gaps_refuse(self):
        history, prices = sample()
        other = replace(history, safe_symbol="OTHER.L")
        self.assertEqual(evaluate(other, prices).reason_code, "INSTRUMENT_MISMATCH")
        # The same nominal price early in the quarter is *not* its closing
        # quarter-end observation. A naive quarter lookup would accept it.
        old_spy = tuple(
            replace(q, as_of=date(q.as_of.year, q.as_of.month - 2, 1))
            for q in prices.spy
        )
        blocked = evaluate(history, replace(prices, spy=old_spy))
        self.assertEqual(blocked.status, "cannot_compute")
        self.assertEqual(blocked.reason_code, "MISSING_HISTORY")

    def test_reset_reason_is_explicit_and_auditable(self):
        history, prices = sample()
        result = evaluate(history, prices)
        row = result.audit_row()
        self.assertIn("reset_reason", row)
        self.assertIn("proposed_growth_target", row)
        self.assertIn("proposed_growth_buy_shares", row)
        self.assertIn("actually_actionable_gbp", row)
        self.assertTrue(all(isinstance(value, str) for value in row.values()))

    @patch("tracker.market_signals.urlopen")
    def test_yahoo_gbp_gbx_mismatch_refused_before_normalizing(self, urlopen):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps({"chart": {"result": [{
            "meta": {"currency": "GBP"},
            "timestamp": [1577923200],
            "indicators": {"quote": [{"close": [123.4]}]},
        }]}}).encode()
        urlopen.return_value.__enter__.return_value = response
        with self.assertRaisesRegex(ValueError, "RAW_UNIT_MISMATCH"):
            fetch_chart("LQQ3.L", date(2020, 1, 1))


class PersistedMarketPriceHoldTests(TestCase):
    @patch("tracker.market_signals.fetch_chart")
    def test_suspicious_batch_is_not_saved_or_used(self, fetch):
        today = timezone.localdate()
        previous = today - timedelta(days=1)
        MarketObservation.objects.create(
            symbol="ERNS.L", as_of=previous, price=D("100"), currency="GBP")
        fetch.return_value = ("GBP", [(today, D("10000"))])
        self.assertIsNone(sync_ticker("ERNS.L", force=True))
        self.assertFalse(MarketObservation.objects.filter(
            symbol="ERNS.L", as_of=today).exists())
        state = YahooSync.objects.get(symbol="ERNS.L")
        self.assertEqual(state.last_error, "PRICE_HOLD:LARGE_MOVE")

    @patch("tracker.market_signals.fetch_chart")
    def test_accepted_batch_can_be_committed(self, fetch):
        today = timezone.localdate()
        fetch.return_value = ("GBP", [(today, D("100"))])
        saved = sync_ticker("ERNS.L", force=True)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.price, D("100"))
        self.assertEqual(YahooSync.objects.get(symbol="ERNS.L").last_error, "")

    @patch("tracker.market_signals.fetch_chart")
    def test_missing_current_quote_is_held_and_does_not_create_history(self, fetch):
        today = timezone.localdate()
        fetch.return_value = ("GBP", [(today - timedelta(days=9), D("100"))])
        self.assertIsNone(sync_ticker("ERNS.L", force=True))
        self.assertFalse(MarketObservation.objects.filter(symbol="ERNS.L").exists())
        self.assertEqual(
            YahooSync.objects.get(symbol="ERNS.L").last_error,
            "PRICE_HOLD:STALE_PRICE")
