"""Pin the exact issuer/GBP trading lines at enrolment; do not re-map old rows."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0009_owner_cockpit")]

    operations = [
        migrations.AddField(
            model_name="strategyinstance",
            name="strategy_version",
            field=models.CharField(blank=True, default="", editable=False, max_length=48),
        ),
        migrations.AddField(
            model_name="strategyinstance",
            name="mapping_version",
            field=models.CharField(blank=True, default="", editable=False, max_length=40),
        ),
        migrations.AddField(
            model_name="strategyinstance",
            name="resolved_instruments",
            field=models.JSONField(blank=True, default=dict, editable=False),
        ),
    ]
