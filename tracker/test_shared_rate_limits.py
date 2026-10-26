"""Database-backed limits and non-secret, deterministic browser protections."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from tracker.models import RateLimitBucket
from tracker.rate_limits import attempts, increment, clear


class RateLimiterTests(TestCase):
    def test_shared_counter_and_expiry(self):
        key = "public-comparison:test"
        self.assertEqual(attempts(key), 0)
        self.assertEqual(increment(key, 120), 1)
        self.assertEqual(increment(key, 120), 2)
        self.assertEqual(attempts(key), 2)
        RateLimitBucket.objects.filter(key=key).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(attempts(key), 0)
        self.assertEqual(increment(key, 120), 1)
        clear(key)
        self.assertEqual(attempts(key), 0)

    def test_login_bucket_keys_do_not_reveal_username(self):
        from django.test import RequestFactory
        from tracker.v2_auth import _failure_keys
        request = RequestFactory().post("/login/", {"username": "someone@example.com"})
        keys = _failure_keys(request)
        self.assertFalse(any("someone" in key for key, _ in keys))
        self.assertEqual(keys, _failure_keys(request))

    def test_browser_permission_policy(self):
        response = self.client.get("/login/")
        self.assertEqual(response["Permissions-Policy"],
                         "camera=(), microphone=(), geolocation=(), payment=()")
        self.assertEqual(response["Cross-Origin-Resource-Policy"], "same-origin")
