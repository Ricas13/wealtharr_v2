from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("tracker", "0005_paper_account")]
    operations = [
        migrations.CreateModel(
            name="StrategyValueSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("as_of", models.DateField()),
                ("total_value", models.DecimalField(decimal_places=2, max_digits=24)),
                ("net_contributed", models.DecimalField(decimal_places=2, max_digits=24)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("strategy", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                               related_name="value_snapshots",
                                               to="tracker.strategyinstance")),
            ],
            options={"ordering": ["as_of"]},
        ),
        migrations.AddConstraint(
            model_name="strategyvaluesnapshot",
            constraint=models.UniqueConstraint(
                fields=("strategy", "as_of"), name="unique_strategy_value_date"),
        ),
    ]
