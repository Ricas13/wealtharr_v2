"""Fixes from the October 2026 bug and security review."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


class SignInTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("owner-x", password="a-long-test-password-1")

    @override_settings(TURNSTILE_REQUIRED=True, TURNSTILE_SITE_KEY="s", TURNSTILE_SECRET_KEY="k")
    def test_a_failed_challenge_never_reveals_whether_the_password_was_right(self):
        with patch("tracker.v2_auth.authenticate", create=True) as never, \
                patch("django.contrib.auth.forms.authenticate") as check:
            right = self.client.post(reverse("login"), {"username": "owner-x", "password": "a-long-test-password-1"})
            wrong = self.client.post(reverse("login"), {"username": "owner-x", "password": "nope"})
        check.assert_not_called()
        never.assert_not_called()
        self.assertEqual(right.status_code, 200)
        self.assertContains(right, "Security verification failed")
        self.assertNotContains(wrong, "correct username and password")

    @override_settings(TURNSTILE_REQUIRED=False)
    def test_repeated_failures_pause_that_username(self):
        for _ in range(10):
            self.client.post(reverse("login"), {"username": "owner-x", "password": "nope"})
        paused = self.client.post(reverse("login"), {"username": "owner-x", "password": "a-long-test-password-1"})
        self.assertContains(paused, "Too many failed sign-ins")
        self.assertFalse(paused.wsgi_request.user.is_authenticated)
        cache.clear()
        ok = self.client.post(reverse("login"), {"username": "owner-x", "password": "a-long-test-password-1"})
        self.assertEqual(ok.status_code, 302)


class PublicCompareBudgetTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_signed_out_fresh_replays_are_capped_per_minute(self):
        from tracker import v2_home
        with patch.object(v2_home, "PUBLIC_FRESH_PER_MINUTE", 2):
            codes = [self.client.get("/compare.json?amount=%d&monthly=0&range=1y" % (500 + i)).status_code
                     for i in range(3)]
            self.assertEqual(codes, [200, 200, 429])
            # Already-computed answers and the default view still work.
            self.assertEqual(self.client.get("/compare.json?amount=500&monthly=0&range=1y").status_code, 200)
            self.assertEqual(self.client.get("/").status_code, 200)
            self.assertEqual(self.client.get("/?amount=999&monthly=0&range=1y").status_code, 200)

    def test_signed_in_comparison_is_not_capped(self):
        from tracker import v2_home
        user = get_user_model().objects.create_user("cmp-owner", password="a-long-test-password-1")
        self.client.force_login(user)
        with patch.object(v2_home, "PUBLIC_FRESH_PER_MINUTE", 0):
            response = self.client.get(reverse("v2_compare_json") + "?amount=777&monthly=0&range=1y")
        self.assertEqual(response.status_code, 200)


class DeliveryChannelLengthTests(TestCase):
    def test_every_delivery_channel_fits_the_column(self):
        from tracker.models import NotificationDelivery
        from tracker.notification_routing import DELIVERY_CHANNELS
        limit = NotificationDelivery._meta.get_field("channel").max_length
        self.assertTrue(all(len(c) <= limit for c in DELIVERY_CHANNELS))


class PeriodCloseTests(TestCase):
    def test_a_review_waits_for_the_period_ends_own_close(self):
        from datetime import date
        from decimal import Decimal as D
        from tracker.market_data import period_close
        from tracker.models import MarketObservation, YahooSync
        end = date(2026, 9, 30)  # a Wednesday
        MarketObservation.objects.create(symbol="ABC.L", as_of=date(2026, 9, 29), price=D("10"), currency="GBP")
        self.assertIsNone(period_close("ABC.L", end))
        MarketObservation.objects.create(symbol="ABC.L", as_of=end, price=D("11"), currency="GBP")
        self.assertEqual(period_close("ABC.L", end).price, D("11"))
        YahooSync.objects.create(symbol="ABC.L", last_error="PRICE_HOLD:LARGE_MOVE")
        self.assertIsNone(period_close("ABC.L", end))

    def test_a_holiday_or_weekend_period_end_uses_the_last_close_once_a_later_one_exists(self):
        from datetime import date
        from decimal import Decimal as D
        from tracker.market_data import period_close
        from tracker.models import MarketObservation
        end = date(2024, 3, 31)  # Sunday; Friday 29th was Good Friday
        MarketObservation.objects.create(symbol="ABC.L", as_of=date(2024, 3, 28), price=D("10"), currency="GBP")
        self.assertIsNone(period_close("ABC.L", end))
        MarketObservation.objects.create(symbol="ABC.L", as_of=date(2024, 4, 2), price=D("12"), currency="GBP")
        self.assertEqual(period_close("ABC.L", end).price, D("10"))

    def test_a_missing_close_on_a_trading_day_is_a_gap_not_a_holiday(self):
        from datetime import date
        from decimal import Decimal as D
        from tracker.market_data import period_close
        from tracker.models import MarketObservation
        end = date(2026, 9, 30)
        for day, price in ((date(2026, 9, 29), "100"), (date(2026, 10, 1), "108")):
            MarketObservation.objects.create(symbol="ABC.L", as_of=day, price=D(price), currency="GBP")
        # Another London line traded on the 30th, so ABC.L's close is missing data.
        MarketObservation.objects.create(symbol="XYZ.L", as_of=end, price=D("5"), currency="GBP")
        self.assertIsNone(period_close("ABC.L", end))
        # A US line trading that day says nothing about London.
        MarketObservation.objects.filter(symbol="XYZ.L").delete()
        MarketObservation.objects.create(symbol="SPY", as_of=end, price=D("500"), currency="USD")
        self.assertEqual(period_close("ABC.L", end).price, D("100"))


class ResetEvidenceCurrencyTests(TestCase):
    def test_dollar_and_euro_resets_find_their_own_currency_closes(self):
        from datetime import date
        from decimal import Decimal as D
        from types import SimpleNamespace
        from tracker.cockpit_execution import verified_quote_evidence
        from tracker.models import MarketObservation
        day = date(2026, 10, 2)
        marks = []
        for symbol in ("IJR", "AGG"):
            MarketObservation.objects.create(symbol=symbol, as_of=day, price=D("100"), currency="USD")
            marks.append(SimpleNamespace(symbol=symbol, as_of=day, price=D("100"), source="yahoo"))
        evidence = verified_quote_evidence(marks, "USD")
        self.assertEqual(evidence["growth"]["currency"], "USD")


class SupersededReviewAlertTests(TestCase):
    def test_a_recalculated_review_gets_fresh_alert_keys_and_old_ones_are_cancelled(self):
        from datetime import date
        from tracker.cockpit_notifications import review_tag
        from tracker.models import (Account, NotificationDelivery, StrategyInstance, StrategyPreset,
                                    StrategySignal)
        from tracker.v2_review_refresh import retire_stale_reviews
        owner = get_user_model().objects.create_user("digest-owner", password="a-long-test-password-1")
        account = Account.objects.create(owner=owner, name="ISA", kind="ISA", country="GB", broker="TRADING212")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target", defaults={"name": "9Sig", "summary": "", "risk": "HIGH", "active": True})
        strategy = StrategyInstance.objects.create(owner=owner, account=account, preset=preset, name="9Sig",
                                                   equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        end = date(2026, 9, 30)
        old = StrategySignal.objects.create(strategy=strategy, kind="QUARTERLY", as_of=end,
                                            signal_key="quarter:2026-Q3", title="t", message="m",
                                            actions=[{"side": "BUY", "symbol": "LQQ3.L", "quantity": "10",
                                                      "estimated_gbp": "100"}])
        self.assertEqual(review_tag(old), "quarter:2026-Q3")
        queued = NotificationDelivery.objects.create(owner=owner, channel="email", status="FAILED",
                                                     event_key="quarterly-digest:%s:quarter:2026-Q3" % strategy.pk)
        retire_stale_reviews(strategy, date(2026, 9, 1))
        queued.refresh_from_db()
        self.assertEqual(queued.status, "CANCELLED")
        new = StrategySignal.objects.create(strategy=strategy, kind="QUARTERLY", as_of=end,
                                            signal_key="quarter:2026-Q3", title="t", message="m2", actions=[])
        self.assertEqual(review_tag(new), "quarter:2026-Q3:%s" % new.pk)


class FillPriceCheckTests(TestCase):
    def test_a_price_far_from_the_review_is_refused(self):
        from tracker.test_g_single_fill import ReviewFixture
        fixture = ReviewFixture("setUp")
        fixture.client = self.client
        fixture.setUp()
        from tracker.models import SignalFill
        self.client.post(fixture.url, fixture.fill(0, "0.4", "10000"))
        self.assertFalse(SignalFill.objects.filter(signal=fixture.signal).exists())
        self.client.post(fixture.url, fixture.fill(0, "0.4", "105"))
        self.assertTrue(SignalFill.objects.filter(signal=fixture.signal).exists())
