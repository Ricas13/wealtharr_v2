"""Scope notification receipts to their source strategy for safe retries."""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0018_quote_raw_provenance"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationdelivery",
            name="strategy",
            field=models.ForeignKey(
                to="tracker.strategyinstance",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="notification_receipts",
                null=True, blank=True,
            ),
        ),
    ]
