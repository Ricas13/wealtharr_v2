from django.db import migrations, models


class Migration(migrations.Migration):
    """"discord_webhook" (15 characters) didn't fit the 12-character channel column on PostgreSQL."""

    dependencies = [("tracker", "0028_discord_direct_messages")]

    operations = [
        migrations.AlterField(
            model_name="notificationdelivery", name="channel", field=models.CharField(max_length=24)),
    ]
