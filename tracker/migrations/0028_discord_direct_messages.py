from django.db import migrations, models


def keep_site_webhook(apps, schema_editor):
    """Whoever had Discord on keeps getting the same channel posts.

    The old site-wide webhook becomes each such person's own webhook
    (ciphertext copied as is), and DMs start off until they connect.
    """
    SystemIntegration = apps.get_model("tracker", "SystemIntegration")
    NotificationPreference = apps.get_model("tracker", "NotificationPreference")
    cfg = SystemIntegration.objects.filter(pk=1).first()
    webhook = cfg.discord_url_ciphertext if cfg else ""
    if webhook:
        NotificationPreference.objects.filter(discord_enabled=True).update(
            discord_webhook_ciphertext=webhook, discord_webhook_enabled=True)
    NotificationPreference.objects.update(discord_enabled=False)


class Migration(migrations.Migration):
    """Discord alerts: a DM from the site's bot and/or each person's own channel webhook."""

    dependencies = [
        ("tracker", "0027_price_revision"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationpreference", name="discord_user_id",
            field=models.CharField(blank=True, max_length=24)),
        migrations.AddField(
            model_name="notificationpreference", name="discord_username",
            field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(
            model_name="notificationpreference", name="discord_connected_at",
            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(
            model_name="notificationpreference", name="discord_webhook_enabled",
            field=models.BooleanField(default=False)),
        migrations.AddField(
            model_name="notificationpreference", name="discord_webhook_ciphertext",
            field=models.TextField(blank=True)),
        migrations.AddField(
            model_name="systemintegration", name="discord_client_id",
            field=models.CharField(blank=True, max_length=24)),
        migrations.AddField(
            model_name="systemintegration", name="discord_client_secret_ciphertext",
            field=models.TextField(blank=True)),
        migrations.AddField(
            model_name="systemintegration", name="discord_bot_token_ciphertext",
            field=models.TextField(blank=True)),
        migrations.AddField(
            model_name="systemintegration", name="discord_guild_id",
            field=models.CharField(blank=True, max_length=24)),
        migrations.RunPython(keep_site_webhook, migrations.RunPython.noop),
        migrations.RemoveField(model_name="systemintegration", name="discord_url_ciphertext"),
    ]
