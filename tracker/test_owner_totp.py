"""Section I: optional second factor must guard every private endpoint."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tracker.models import OwnerTOTP
from tracker.totp import current_code, matching_step, verify_account_code


TIME_A = 1_000_000_000


class OwnerAuthenticatorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="totp-admin", email="totp@example.test",
            password="secure-setup-password-123")
        self.other = get_user_model().objects.create_user(
            username="totp-other", password="other-long-password-123")
        self.client.force_login(self.user)

    def enroll(self):
        res = self.client.get(reverse("totp_setup"))
        self.assertEqual(res.status_code, 200)
        secret = self.client.session["wealtharr_pending_totp"]
        with patch("tracker.totp.time.time", return_value=TIME_A):
            res = self.client.post(reverse("totp_setup"), {
                "intent": "enable", "password": "secure-setup-password-123",
                "code": current_code(secret),
            })
        self.assertRedirects(res, reverse("totp_setup"))
        return secret

    def test_unenrolled_owners_remain_password_only(self):
        self.assertEqual(self.client.get(reverse("cockpit_today")).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("cockpit_today")).status_code, 200)

    def test_authenticator_secret_encrypted_and_second_step_required(self):
        secret = self.enroll()
        factor = OwnerTOTP.objects.get(owner=self.user)
        self.assertNotIn(secret, factor.secret_ciphertext)
        self.assertEqual(factor.last_verified_step, TIME_A // 30)
        self.assertEqual(self.client.get(reverse("django-admin:index")).status_code, 200)
        self.client.logout()
        self.client.force_login(self.user)
        for url in (reverse("cockpit_today"), reverse("dashboard"),
                    reverse("django-admin:index")):
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), reverse("totp_challenge"))
        self.assertEqual(self.client.get(reverse("totp_challenge")).status_code, 200)
        with patch("tracker.totp.time.time", return_value=TIME_A + 30):
            res = self.client.post(reverse("totp_challenge"), {
                "code": current_code(secret)})
        self.assertRedirects(res, reverse("cockpit_today"))
        self.assertEqual(self.client.get(reverse("cockpit_today")).status_code, 200)
        with patch("tracker.totp.time.time", return_value=TIME_A + 30):
            self.assertFalse(verify_account_code(self.user, current_code(secret)))

    def test_setup_requires_password_and_valid_code(self):
        response = self.client.get(reverse("totp_setup"))
        secret = self.client.session["wealtharr_pending_totp"]
        with patch("tracker.totp.time.time", return_value=TIME_A):
            response = self.client.post(reverse("totp_setup"), {
                "intent": "enable", "password": "wrong-password",
                "code": current_code(secret),
            })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OwnerTOTP.objects.filter(owner=self.user).exists())
        with patch("tracker.totp.time.time", return_value=TIME_A):
            response = self.client.post(reverse("totp_setup"), {
                "intent": "enable", "password": "secure-setup-password-123",
                "code": "123",
            })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OwnerTOTP.objects.filter(owner=self.user).exists())

    def test_bad_codes_lock_account_factor_across_sessions(self):
        secret = self.enroll()
        for _ in range(5):
            self.assertFalse(verify_account_code(self.user, "999"))
        factor = OwnerTOTP.objects.get(owner=self.user)
        self.assertEqual(factor.failed_attempts, 5)
        self.assertIsNotNone(factor.locked_until)
        with patch("tracker.totp.time.time", return_value=TIME_A + 60):
            self.assertFalse(verify_account_code(self.user, current_code(secret)))

    def test_disable_requires_both_password_and_fresh_code(self):
        secret = self.enroll()
        with patch("tracker.totp.time.time", return_value=TIME_A + 30):
            response = self.client.post(reverse("totp_setup"), {
                "intent": "disable", "password": "wrong",
                "code": current_code(secret)})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(OwnerTOTP.objects.filter(owner=self.user).exists())
        with patch("tracker.totp.time.time", return_value=TIME_A + 30):
            response = self.client.post(reverse("totp_setup"), {
                "intent": "disable", "password": "secure-setup-password-123",
                "code": current_code(secret)})
        self.assertRedirects(response, reverse("totp_setup"))
        self.assertFalse(OwnerTOTP.objects.filter(owner=self.user).exists())
