"""Preserve alert category so queued/retried notices respect owner opt-outs."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0013_notification_matrix_scheduler")]

    operations = [
        migrations.AddField(
            model_name="notificationdelivery",
            name="category",
            field=models.CharField(max_length=16, blank=True, default=""),
        ),
    ]
