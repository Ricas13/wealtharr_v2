"""Atomically re-encrypt credentials after enabling the independent data key."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from tracker.models import NotificationPreference, OwnerTOTP, SystemIntegration
from tracker.services import decrypt_secret, encrypt_secret

_FIELDS = (
    (SystemIntegration, ("smtp_password_ciphertext", "discord_client_secret_ciphertext",
                         "discord_bot_token_ciphertext", "telegram_bot_token_ciphertext")),
    (NotificationPreference, ("discord_webhook_ciphertext",)),
    (OwnerTOTP, ("secret_ciphertext",)),
)


class Command(BaseCommand):
    help = "Migrate all stored encrypted credentials to DATA_ENCRYPTION_KEY"

    def handle(self, *args, **options):
        if not settings.DATA_ENCRYPTION_KEY:
            raise CommandError("Independent DATA_ENCRYPTION_KEY_FILE required; no changes made.")
        if settings.DATA_ENCRYPTION_KEY == settings.SECRET_KEY:
            raise CommandError("Independent key must differ from Django SECRET_KEY.")
        touched = 0
        with transaction.atomic():
            for model, fields in _FIELDS:
                for row in model.objects.select_for_update().all():
                    updated = []
                    for field in fields:
                        ciphertext = getattr(row, field)
                        if ciphertext:
                            setattr(row, field, encrypt_secret(decrypt_secret(ciphertext)))
                            updated.append(field)
                    if updated:
                        row.save(update_fields=updated)
                        touched += 1
        self.stdout.write("Re-encrypted credential records: %d" % touched)
