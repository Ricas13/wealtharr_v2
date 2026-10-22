"""Dedicated encryption key and legacy ciphertext migration."""
from django.core.management import call_command
from django.test import TestCase, override_settings
from tracker.models import NotificationPreference, SystemIntegration
from tracker.services import encrypt_secret, decrypt_secret


class DataEncryptionKeyTests(TestCase):
    def test_legacy_secrets_survive_django_key_rotation_after_migration(self):
        with override_settings(SECRET_KEY="old-signing-key", DATA_ENCRYPTION_KEY=""):
            legacy = encrypt_secret("smtp-test-password")
            webhook = encrypt_secret("https://example.invalid/secret")
        SystemIntegration.objects.create(pk=1, smtp_password_ciphertext=legacy)
        from django.contrib.auth import get_user_model
        owner = get_user_model().objects.create_user("data-key-user", password="long-test-password-123")
        pref = NotificationPreference.objects.create(owner=owner, discord_webhook_ciphertext=webhook)
        with override_settings(SECRET_KEY="old-signing-key", DATA_ENCRYPTION_KEY="separate-data-key"):
            self.assertEqual(decrypt_secret(legacy), "smtp-test-password")
            call_command("migrate_data_encryption", verbosity=0)
        pref.refresh_from_db()
        integration = SystemIntegration.objects.get(pk=1)
        with override_settings(SECRET_KEY="different-django-key", DATA_ENCRYPTION_KEY="separate-data-key"):
            self.assertEqual(decrypt_secret(integration.smtp_password_ciphertext), "smtp-test-password")
            self.assertEqual(decrypt_secret(pref.discord_webhook_ciphertext),
                             "https://example.invalid/secret")
