from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from io import StringIO

from tracker.models import SystemIntegration
from tracker.services import decrypt_secret, encrypt_secret, readable_with_current_key

User = get_user_model()
OLD, NEW = "old-data-key-aaaaaaaaaaaaaaaaaaaaaaaa", "new-data-key-bbbbbbbbbbbbbbbbbbbbbbbb"


class RateLimitTests(TestCase):
    def setUp(self):
        User.objects.create_user("pat", password="pw-User-1234")
        self.client.login(username="pat", password="pw-User-1234")

    @override_settings(WRITE_RATE_LIMIT_PER_MINUTE=3)
    def test_writes_are_capped_per_user_but_reads_are_not(self):
        codes = [self.client.post("/settings/password/", {}).status_code for _ in range(5)]
        self.assertEqual(codes[:3] != [429] * 3, True)
        self.assertEqual(codes[3:], [429, 429])
        self.assertEqual(self.client.get("/settings/").status_code, 200)

    @override_settings(WRITE_RATE_LIMIT_PER_MINUTE=3)
    def test_another_user_has_their_own_allowance(self):
        for _ in range(5):
            self.client.post("/settings/password/", {})
        User.objects.create_user("eve", password="pw-User-1234")
        other = self.client_class()
        other.login(username="eve", password="pw-User-1234")
        self.assertNotEqual(other.post("/settings/password/", {}).status_code, 429)

    @override_settings(WRITE_RATE_LIMIT_PER_MINUTE=0)
    def test_zero_turns_it_off(self):
        for _ in range(10):
            self.assertNotEqual(self.client.post("/settings/password/", {}).status_code, 429)


class KeyRotationTests(TestCase):
    def test_rotation_reads_old_records_then_moves_them_to_the_new_key(self):
        with override_settings(DATA_ENCRYPTION_KEY=OLD, DATA_ENCRYPTION_KEYS_PREVIOUS=[]):
            cfg = SystemIntegration.current()
            cfg.smtp_password_ciphertext = encrypt_secret("hunter2")
            cfg.save()
        with override_settings(DATA_ENCRYPTION_KEY=NEW, DATA_ENCRYPTION_KEYS_PREVIOUS=[OLD]):
            cfg = SystemIntegration.current()
            self.assertEqual(decrypt_secret(cfg.smtp_password_ciphertext), "hunter2")
            self.assertFalse(readable_with_current_key(cfg.smtp_password_ciphertext))
            out = StringIO()
            call_command("migrate_data_encryption", "--check", stdout=out)
            self.assertIn("not on the current key: 1", out.getvalue())
            call_command("migrate_data_encryption", stdout=StringIO())
            cfg.refresh_from_db()
            self.assertTrue(readable_with_current_key(cfg.smtp_password_ciphertext))
        with override_settings(DATA_ENCRYPTION_KEY=NEW, DATA_ENCRYPTION_KEYS_PREVIOUS=[]):
            self.assertEqual(decrypt_secret(SystemIntegration.current().smtp_password_ciphertext), "hunter2")
