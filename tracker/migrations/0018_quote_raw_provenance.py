"""Original provider/unit evidence; prior normalized observations stay unverified."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0017_ledger_revision")]

    operations = [
        migrations.AddField(
            model_name="marketobservation", name="source",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="marketobservation", name="raw_quote_unit",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="marketobservation", name="raw_close",
            field=models.DecimalField(blank=True, decimal_places=8,
                                      max_digits=22, null=True),
        ),
        migrations.AddField(
            model_name="marketobservation", name="raw_unit_verified",
            field=models.BooleanField(default=False),
        ),
    ]
