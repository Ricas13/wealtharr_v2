from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0006_strategy_value_snapshots")]
    operations = [
        migrations.AddField(
            model_name="strategysignal", name="review_outcome",
            field=models.CharField(
                max_length=16, default="", blank=True,
                choices=[
                    ("CONFIRMED", "Confirmed after review"),
                    ("FALSE_POSITIVE", "False signal"),
                    ("NOT_APPLICABLE", "Not applicable"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="strategysignal", name="review_note",
            field=models.TextField(blank=True, default=""),
        ),
    ]
