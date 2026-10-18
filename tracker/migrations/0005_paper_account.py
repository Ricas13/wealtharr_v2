from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0004_strategy_baselines")]
    operations = [
        migrations.AlterField(
            model_name="account", name="kind",
            field=models.CharField(
                max_length=12, default="ISA",
                choices=[
                    ("ISA", "Stocks & Shares ISA"),
                    ("GIA", "General investment account"),
                    ("SIPP", "SIPP / pension"),
                    ("PAPER", "Paper tracking (virtual only)"),
                    ("OTHER", "Other account"),
                ],
            ),
        ),
    ]
