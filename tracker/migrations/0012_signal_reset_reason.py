"""Section G: explicit reset identity; historic text-only notices remain unverified."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0011_signal_fill")]

    operations = [
        migrations.AddField(
            model_name="strategysignal",
            name="reset_reason",
            field=models.CharField(max_length=240, blank=True, default=""),
        ),
    ]
