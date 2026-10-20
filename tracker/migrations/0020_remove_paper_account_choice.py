"""Remove the obsolete paper account choice without deleting existing records.

Migration 0005 remains in history because later migrations depend on it.
This schema change prevents new UI selections; historical rows are preserved.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0019_notification_strategy_scope")]

    operations = [
        migrations.AlterField(
            model_name="account",
            name="kind",
            field=models.CharField(
                max_length=12,
                default="ISA",
                choices=[
                    ("ISA", "Stocks & Shares ISA"),
                    ("GIA", "General investment account"),
                    ("SIPP", "SIPP / pension"),
                    ("OTHER", "Other account"),
                ],
            ),
        ),
    ]
