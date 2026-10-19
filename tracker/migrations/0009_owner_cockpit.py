from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("tracker", "0008_account_country_broker")]

    operations = [
        migrations.CreateModel(
            name="RebalanceExecution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("executed_on", models.DateField()),
                ("completed_at", models.DateTimeField(auto_now_add=True)),
                ("signal", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    related_name="execution", to="tracker.strategysignal")),
                ("strategy", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    to="tracker.strategyinstance")),
                ("baseline", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT, to="tracker.strategybaseline")),
            ],
        ),
        migrations.CreateModel(
            name="BrokerImportRow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fingerprint", models.CharField(max_length=64)),
                ("source", models.CharField(default="trading212_csv", max_length=24)),
                ("imported_at", models.DateTimeField(auto_now_add=True)),
                ("ledger_entry", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    to="tracker.ledgerentry")),
                ("strategy", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    to="tracker.strategyinstance")),
            ],
        ),
        migrations.AddConstraint(
            model_name="brokerimportrow",
            constraint=models.UniqueConstraint(fields=("strategy", "fingerprint"), name="unique_t212_event"),
        ),
    ]
