"""V2 immutable strategy-instance enrolment and owner-reported opening snapshots."""
from decimal import Decimal
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("tracker", "0015_owner_totp")]

    operations = [
        migrations.AddField(
            model_name="strategyinstance", name="strategy_key",
            field=models.CharField(blank=True, default="", editable=False, max_length=100),
        ),
        migrations.AddField(
            model_name="strategyinstance", name="platform_code",
            field=models.CharField(blank=True, default="", editable=False, max_length=32),
        ),
        migrations.AddField(
            model_name="strategyinstance", name="account_type_code",
            field=models.CharField(blank=True, default="", editable=False, max_length=12),
        ),
        migrations.AddField(
            model_name="strategyinstance", name="started_on",
            field=models.DateField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="strategyinstance", name="owner_checked_on",
            field=models.DateField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="strategyinstance", name="tracking_only",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="strategyinstance", name="alert_routes",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="strategyinstance", name="onboarding_mode",
            field=models.CharField(blank=True, default="", editable=False, max_length=12),
        ),
        migrations.CreateModel(
            name="InstanceOpeningSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reported_on", models.DateField()),
                ("cash_gbp", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=20)),
                ("positions", models.JSONField(default=dict)),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("strategy", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="opening_snapshot", to="tracker.strategyinstance"
                )),
            ],
        ),
    ]
