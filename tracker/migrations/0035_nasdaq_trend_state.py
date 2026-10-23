"""Versioned progress for Nasdaq Trend + DCA daily signals."""
from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0034_shared_rate_limits"),
    ]

    operations = [
        migrations.CreateModel(
            name="NasdaqTrendState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("last_as_of", models.DateField(blank=True, null=True)),
                ("regime", models.CharField(default="UNKNOWN", max_length=8)),
                ("guard", models.CharField(default="NONE", max_length=16)),
                ("off_since", models.DateField(blank=True, null=True)),
                ("last_target", models.CharField(blank=True, default="", max_length=16)),
                ("reserve", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=20)),
                ("reserve_source", models.CharField(blank=True, default="", max_length=32)),
                ("completed_instalments", models.PositiveSmallIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("strategy", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                                related_name="nasdaq_trend_state", to="tracker.strategyinstance")),
            ],
        ),
    ]
