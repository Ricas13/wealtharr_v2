"""Phase 7: owner reminder interval and persisted per-instance delivery identity."""
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("tracker", "0018_quote_raw_provenance")]

    operations = [
        migrations.AddField(
            model_name="notificationpreference", name="unacted_reminder_days",
            field=models.PositiveSmallIntegerField(default=3),
        ),
        migrations.AddField(
            model_name="notificationpreference", name="isa_allowance_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="notificationdelivery", name="strategy",
            field=models.ForeignKey(blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="alert_deliveries", to="tracker.strategyinstance"),
        ),
    ]
