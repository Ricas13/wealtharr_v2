"""Only unit-verified Yahoo closes may drive an instruction or certify evidence."""
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.test import TestCase

from tracker import market_data
from tracker.cockpit_execution import verified_quote_evidence
from tracker.market_data import FetchedChart, period_close, settlement_close, sync_ticker
from tracker.models import MarketObservation, PriceMark, YahooSync

DAY = date(2026, 9, 30)
RECENT = timezone.localdate() - timedelta(days=1)  # stale closes are held


def obs(symbol="VUAG.L", day=DAY, price="100", verified=True, currency="GBP"):
    return MarketObservation.objects.create(
        symbol=symbol, as_of=day, price=D(price), currency=currency,
        source="yahoo" if verified else "", raw_unit_verified=verified,
        raw_quote_unit="GBP" if verified else "",
        raw_close=D(price) if verified else None)


class UnverifiedQuotesAreIgnoredTests(TestCase):
    def test_settlement_and_period_close_skip_unverified_rows(self):
        obs(verified=False)
        self.assertIsNone(settlement_close("VUAG.L", DAY))
        self.assertIsNone(period_close("VUAG.L", DAY))
        obs("VUAG.L", DAY - timedelta(days=1), "99")
        self.assertEqual(settlement_close("VUAG.L", DAY).price, D("99"))

    def test_verified_row_is_used(self):
        obs()
        self.assertEqual(settlement_close("VUAG.L", DAY).price, D("100"))

    def test_reset_evidence_refuses_an_unverified_observation(self):
        obs(verified=False)
        mark = PriceMark(symbol="VUAG.L", as_of=DAY, price=D("100"), source="yahoo")
        with self.assertRaises(ValidationError):
            verified_quote_evidence([mark, mark])
        MarketObservation.objects.all().delete()
        obs()
        self.assertEqual(verified_quote_evidence([mark, mark])["growth"]["symbol"], "VUAG.L")


class LegacyRowsHealTests(TestCase):
    def fetch(self, price="100"):
        series = [(RECENT, D(price))]
        return FetchedChart("GBP", series, "GBP", {RECENT: D(price)})

    def sync(self, price="100"):
        with patch("tracker.market_data.fetch_chart", return_value=self.fetch(price)) as call:
            sync_ticker("VUAG.L", force=True)
        return call

    def test_a_legacy_row_is_upgraded_when_yahoo_confirms_its_price(self):
        legacy = obs(day=RECENT, verified=False)
        self.sync("100")
        legacy.refresh_from_db()
        self.assertTrue(legacy.raw_unit_verified)
        self.assertEqual((legacy.source, legacy.raw_quote_unit, legacy.raw_close),
                         ("yahoo", "GBP", D("100")))

    def test_a_materially_different_price_is_held_not_upgraded(self):
        legacy = obs(day=RECENT, price="100", verified=False)
        self.sync("50")  # a 50% revision is held for the owner, never auto-accepted
        legacy.refresh_from_db()
        self.assertFalse(legacy.raw_unit_verified)
        self.assertEqual(legacy.price, D("100"))

    def test_a_small_vendor_correction_is_logged_then_verified(self):
        from tracker.models import PriceRevision
        legacy = obs(day=RECENT, price="100", verified=False)
        self.sync("95")
        legacy.refresh_from_db()
        self.assertEqual((legacy.price, legacy.raw_unit_verified), (D("95"), True))
        self.assertTrue(PriceRevision.objects.filter(symbol="VUAG.L").exists())

    def test_unverified_history_triggers_one_full_fetch(self):
        obs(day=RECENT, verified=False)
        call = self.sync("100")
        self.assertEqual(call.call_args.args[1], date(2010, 1, 1))
        call = self.sync("100")  # now healed: back to the short top-up window
        self.assertGreater(call.call_args.args[1], date(2010, 1, 1))
