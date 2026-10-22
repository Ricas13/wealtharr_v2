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
