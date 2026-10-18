from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("tracker", "0001_initial")]
    operations = [
        migrations.AddField(model_name="strategyinstance", name="equity_symbol", field=models.CharField(max_length=24, blank=True, help_text="Exact Yahoo ticker of the 3× equity trading line")),
        migrations.AddField(model_name="strategyinstance", name="safe_symbol", field=models.CharField(max_length=24, blank=True, help_text="Exact Yahoo ticker of the safe asset")),
        migrations.AddField(model_name="strategyinstance", name="market_data_enabled", field=models.BooleanField(default=False, help_text="Fetch Yahoo closes and enable advisory strategy reviews")),
        migrations.AddField(model_name="pricemark", name="source", field=models.CharField(max_length=10, default="manual", choices=[("manual", "Manual"), ("yahoo", "Yahoo")])),
        migrations.CreateModel(name="YahooSync", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("symbol", models.CharField(max_length=24, unique=True)),
            ("last_attempt", models.DateTimeField(blank=True, null=True)),
            ("last_success", models.DateTimeField(blank=True, null=True)),
            ("last_error", models.CharField(max_length=80, blank=True)),
        ]),
        migrations.CreateModel(name="MarketObservation", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("symbol", models.CharField(max_length=24)),
            ("as_of", models.DateField()),
            ("price", models.DecimalField(decimal_places=8, max_digits=22)),
            ("currency", models.CharField(max_length=3)),
        ], options={"ordering": ["as_of"]}),
        migrations.CreateModel(name="StrategySignal", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("signal_key", models.CharField(max_length=80)),
            ("kind", models.CharField(max_length=20)),
            ("title", models.CharField(max_length=180)),
            ("message", models.TextField()),
            ("actions", models.JSONField(default=list)),
            ("as_of", models.DateField()),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
            ("strategy", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="signals", to="tracker.strategyinstance")),
        ], options={"ordering": ["-created_at"]}),
        migrations.AddConstraint(model_name="marketobservation", constraint=models.UniqueConstraint(fields=("symbol", "as_of"), name="unique_market_close")),
        migrations.AddConstraint(model_name="strategysignal", constraint=models.UniqueConstraint(fields=("strategy", "signal_key"), name="unique_strategy_signal")),
    ]
