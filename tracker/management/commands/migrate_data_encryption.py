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
    help = ("Migrate all stored encrypted credentials to DATA_ENCRYPTION_KEY (also the last step of a key "
            "rotation: put the old key in DATA_ENCRYPTION_KEYS_PREVIOUS, set the new one, run this).")

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true",
                            help="Only count credentials not yet on the current key; change nothing.")

    def handle(self, *args, **options):
        if options["check"]:
            from tracker.services import readable_with_current_key
            behind = sum(1 for model, fields in _FIELDS for row in model.objects.all() for f in fields
                         if getattr(row, f) and not readable_with_current_key(getattr(row, f)))
            self.stdout.write("Credentials not on the current key: %d" % behind)
            return
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
