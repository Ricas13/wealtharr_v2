"""Section I: optional MFA, confidential secrets and fail-closed restore controls."""
import os
import shutil
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from tracker.models import OwnerTOTP
from tracker.services import decrypt_secret
from tracker.totp import current_code, generate_secret, verify_account_code
from wealtharr import settings as site_settings

ROOT = Path(__file__).resolve().parent.parent


class ProductionFileSafetyTests(SimpleTestCase):
    def test_secret_file_reads_stable_bytes_and_never_uses_url_encoding(self):
        with TemporaryDirectory() as folder:
            file = Path(folder) / "key"
            file.write_text("a-long-test-key-with:%/$-and-punctuation\n", encoding="utf-8")
            with patch.dict(os.environ, {"SECRET_KEY_FILE": str(file)}):
                self.assertEqual(site_settings._read_secret_file("SECRET_KEY_FILE"),
                                 "a-long-test-key-with:%/$-and-punctuation")

    def test_empty_file_fails_closed(self):
        with TemporaryDirectory() as folder:
            file = Path(folder) / "empty"
            file.write_text("\n")
            with patch.dict(os.environ, {"DATABASE_PASSWORD_FILE": str(file)}):
                with self.assertRaises(RuntimeError):
                    site_settings._read_secret_file("DATABASE_PASSWORD_FILE")

    def test_production_deploy_never_embeds_plaintext_env_or_archives_in_image(self):
        docker_ignore = (ROOT / ".dockerignore").read_text()
        compose = (ROOT / "compose.yml").read_text()
        self.assertIn("/secrets", docker_ignore)
        self.assertIn("/backups", docker_ignore)
        self.assertIn("POSTGRES_PASSWORD_FILE:", compose)
        self.assertIn("DATABASE_PASSWORD_FILE:", compose)
        self.assertIn("SECRET_KEY_FILE:", compose)
        self.assertIn("127.0.0.1:8088:8000", compose)
        self.assertNotIn("postgresql://wealtharr:", compose)
        self.assertIn("pg_restore --list", compose)
        self.assertIn("sha256sum", compose)

    def test_recovery_tools_use_guarded_offline_verification(self):
        verify = (ROOT / "scripts/verify_backup.sh").read_text()
        recovery = (ROOT / "scripts/restore_backup.sh").read_text()
        self.assertIn("--network none", verify)
        self.assertIn("pg_restore", verify)
        self.assertIn("WEALTHARR_CONFIRM_RESTORE", recovery)
        self.assertIn("verify_backup.sh", recovery)
        self.assertLess(recovery.index("verify_backup.sh"), recovery.index("docker compose stop"))
        self.assertIn("--single-transaction", recovery)
        if shutil.which("bash"):
            for script in ("verify_backup.sh", "restore_backup.sh"):
                result = subprocess.run(
                    ["bash", "-n", str(ROOT / "scripts" / script)],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


class OwnerAuthenticatorTests(TestCase):
    password = "strong-test-owner-password-2026"

    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "private_i_owner", password=self.password)
        self.client.force_login(self.owner)

    def test_enrollment_requires_password_and_valid_code_and_encrypts_session_seed(self):
        setup = reverse("totp_setup")
        response = self.client.get(setup)
        self.assertEqual(response.status_code, 200)
        seed = response.context["setup_secret"]
        self.assertEqual(len(seed), 32)
        ciphertext = self.client.session["wealtharr_pending_totp"]
        self.assertNotEqual(ciphertext, seed)
        self.assertEqual(decrypt_secret(ciphertext), seed)
        incorrect = self.client.post(setup, {
            "intent": "enable", "code": "000000", "password": self.password + "-wrong"})
        self.assertEqual(incorrect.status_code, 200)
        self.assertFalse(OwnerTOTP.objects.filter(owner=self.owner).exists())
        response = self.client.post(setup, {
            "intent": "enable", "code": current_code(seed), "password": self.password})
        self.assertRedirects(response, setup)
        factor = OwnerTOTP.objects.get(owner=self.owner)
        self.assertNotEqual(factor.secret_ciphertext, seed)
        self.assertEqual(decrypt_secret(factor.secret_ciphertext), seed)

    @override_settings(TURNSTILE_REQUIRED=False)
    def test_authenticated_private_routes_and_admin_blocked_until_second_step(self):
        seed = generate_secret()
        from django.utils import timezone
        from tracker.services import encrypt_secret
        OwnerTOTP.objects.create(
            owner=self.owner, secret_ciphertext=encrypt_secret(seed),
            enabled_at=timezone.now())
        self.client.post(reverse("logout"))
        signed_in = self.client.post(reverse("login"), {
            "username": self.owner.username, "password": self.password})
        self.assertEqual(signed_in.status_code, 302)
        for route in (reverse("cockpit_today"), reverse("cockpit_settings"),
                      reverse("admin:index")):
            response = self.client.get(route)
            self.assertRedirects(response, reverse("totp_challenge"),
                                 fetch_redirect_response=False)
        unlocked = self.client.post(reverse("totp_challenge"), {"code": current_code(seed)})
        self.assertRedirects(unlocked, reverse("cockpit_today"),
                             fetch_redirect_response=False)
        self.assertNotEqual(self.client.get(reverse("cockpit_settings")).status_code, 302)

    def test_factor_rejects_replay_and_locks_after_invalid_attempts(self):
        from django.utils import timezone
        from tracker.services import encrypt_secret
        seed = generate_secret()
        OwnerTOTP.objects.create(
            owner=self.owner, secret_ciphertext=encrypt_secret(seed),
            enabled_at=timezone.now())
        current = int(time.time())
        code = current_code(seed, at_time=current)
        with patch("tracker.totp.time.time", return_value=current):
            self.assertTrue(verify_account_code(self.owner, code))
            self.assertFalse(verify_account_code(self.owner, code))
            for _ in range(4):
                self.assertFalse(verify_account_code(self.owner, "invalid"))
        factor = OwnerTOTP.objects.get(owner=self.owner)
        self.assertEqual(factor.failed_attempts, 5)
        self.assertIsNotNone(factor.locked_until)
        with patch("tracker.totp.time.time", return_value=current + 30):
            self.assertFalse(verify_account_code(
                self.owner, current_code(seed, at_time=current + 30)))

    def test_no_factor_preserves_one_step_password_login(self):
        self.client.post(reverse("logout"))
        with override_settings(TURNSTILE_REQUIRED=False):
            result = self.client.post(reverse("login"), {
                "username": self.owner.username, "password": self.password})
        self.assertRedirects(result, reverse("cockpit_today"),
                             fetch_redirect_response=False)
