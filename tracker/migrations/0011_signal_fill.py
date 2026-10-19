"""Track each owner-confirmed, possibly partial fill without changing ledger totals."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0010_enrolled_instrument_manifest"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SignalFill",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("action_index", models.PositiveSmallIntegerField()),
                ("idempotency_key", models.CharField(max_length=36)),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("ledger_entry", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="confirmed_signal_fill", to="tracker.ledgerentry")),
                ("recorded_by", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    to=settings.AUTH_USER_MODEL)),
                ("signal", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="confirmed_fills", to="tracker.strategysignal")),
                ("strategy", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="signal_fills", to="tracker.strategyinstance")),
            ],
            options={"ordering": ["ledger_entry__occurred_on", "pk"]},
        ),
        migrations.AddConstraint(
            model_name="signalfill",
            constraint=models.UniqueConstraint(
                fields=("signal", "idempotency_key"),
                name="unique_signal_fill_request"),
        ),
    ]
