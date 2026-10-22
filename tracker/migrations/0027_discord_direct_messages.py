from django.db import migrations, models


class Migration(migrations.Migration):
    """Discord alerts become private DMs from a bot; the channel webhook is retired."""

    dependencies = [
        ("tracker", "0026_more_account_types"),
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
        migrations.RemoveField(model_name="systemintegration", name="discord_url_ciphertext"),
        # Nobody has a connected Discord account yet, so webhook-era switches are off.
        migrations.RunSQL(
            "UPDATE tracker_notificationpreference SET discord_enabled = FALSE",
            reverse_sql=migrations.RunSQL.noop),
    ]
