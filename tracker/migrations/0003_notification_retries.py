from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("tracker", "0002_market_signals")]
    operations = [
        migrations.AddField(model_name="notificationdelivery", name="subject",
                            field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name="notificationdelivery", name="message",
                            field=models.TextField(blank=True)),
        migrations.AddField(model_name="notificationdelivery", name="attempts",
                            field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="notificationdelivery", name="last_attempt_at",
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="notificationdelivery", name="next_retry_at",
                            field=models.DateTimeField(blank=True, null=True)),
    ]
