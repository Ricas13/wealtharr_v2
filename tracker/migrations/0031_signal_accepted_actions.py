from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0030_telegram")]

    operations = [
        migrations.AddField("strategysignal", "accepted_actions", models.JSONField(default=dict, blank=True)),
    ]
