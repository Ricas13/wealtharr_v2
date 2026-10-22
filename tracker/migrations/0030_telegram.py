from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0029_delivery_channel_length")]

    operations = [
        migrations.AddField("notificationpreference", "telegram_enabled", models.BooleanField(default=False)),
        migrations.AddField("notificationpreference", "telegram_chat_id", models.CharField(max_length=24, blank=True)),
        migrations.AddField("notificationpreference", "telegram_username", models.CharField(max_length=100, blank=True)),
        migrations.AddField("notificationpreference", "telegram_connected_at", models.DateTimeField(null=True, blank=True)),
        migrations.AddField("notificationpreference", "telegram_link_code", models.CharField(max_length=64, blank=True)),
        migrations.AddField("notificationpreference", "telegram_link_expires", models.DateTimeField(null=True, blank=True)),
        migrations.AddField("systemintegration", "telegram_bot_token_ciphertext", models.TextField(blank=True)),
        migrations.AddField("systemintegration", "telegram_bot_username", models.CharField(max_length=64, blank=True)),
    ]
