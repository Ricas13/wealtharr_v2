"""Pin the rules version used to create each quarterly or warning signal."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0015_owner_totp")]

    operations = [
        migrations.AddField(
            model_name="strategysignal",
            name="strategy_version",
            field=models.CharField(blank=True, default="", max_length=48),
        ),
    ]
