"""Migration 0028 keeps the old site-wide webhook for everyone who had Discord on."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class KeepSiteWebhookMigrationTests(TransactionTestCase):
    before = [("tracker", "0027_price_revision")]
    after = [("tracker", "0028_discord_direct_messages")]

    def test_old_webhook_becomes_each_discord_users_own(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        old = executor.loader.project_state(self.before).apps
        User = old.get_model("auth", "User")
        on = User.objects.create(username="on")
        off = User.objects.create(username="off")
        old.get_model("tracker", "SystemIntegration").objects.create(pk=1, discord_url_ciphertext="CIPHER")
        Pref = old.get_model("tracker", "NotificationPreference")
        Pref.objects.create(owner_id=on.pk, discord_enabled=True)
        Pref.objects.create(owner_id=off.pk, discord_enabled=False)

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.after)
        new = executor.loader.project_state(self.after).apps.get_model("tracker", "NotificationPreference")
        kept, untouched = new.objects.get(owner_id=on.pk), new.objects.get(owner_id=off.pk)
        self.assertEqual((kept.discord_webhook_enabled, kept.discord_webhook_ciphertext, kept.discord_enabled),
                         (True, "CIPHER", False))
        self.assertEqual((untouched.discord_webhook_enabled, untouched.discord_webhook_ciphertext), (False, ""))

        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
