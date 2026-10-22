# Shared, database-atomic security throttles across Gunicorn workers.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0033_dividend_events")]

    operations = [
        migrations.CreateModel(
            name="RateLimitBucket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("key", models.CharField(max_length=180, unique=True)),
                ("count", models.PositiveIntegerField(default=0)),
                ("expires_at", models.DateTimeField(db_index=True)),
            ],
        ),
    ]
