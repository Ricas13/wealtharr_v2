from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("tracker", "0003_notification_retries")]
    operations = [
        migrations.CreateModel(
            name="StrategyBaseline",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                          serialize=False, verbose_name="ID")),
                ("as_of", models.DateField()),
                ("equity_value", models.DecimalField(max_digits=22, decimal_places=2)),
                ("equity_quantity", models.DecimalField(max_digits=22, decimal_places=8)),
                ("safe_value", models.DecimalField(max_digits=22, decimal_places=2)),
                ("cash_value", models.DecimalField(max_digits=22, decimal_places=2)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("strategy", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                               related_name="baselines",
                                               to="tracker.strategyinstance")),
                ("reset_signal", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                    to="tracker.strategysignal")),
            ],
            options={"ordering": ["as_of", "id"]},
        ),
        migrations.AddConstraint(
            model_name="strategybaseline",
            constraint=models.UniqueConstraint(fields=("strategy", "as_of"),
                                                name="unique_strategy_baseline"),
        ),
    ]
