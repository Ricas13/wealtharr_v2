"""Section H — owner alert category/channel choices and scheduler diagnostics."""
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("tracker", "0012_signal_reset_reason")]
    operations = [
        migrations.AddField(model_name="notificationpreference", name="quarterly_email", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="notificationpreference", name="quarterly_discord", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="notificationpreference", name="urgent_email", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="notificationpreference", name="urgent_discord", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="notificationpreference", name="data_email", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="notificationpreference", name="data_discord", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="notificationpreference", name="followup_email", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="notificationpreference", name="followup_discord", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="notificationpreference", name="contribution_email", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="notificationpreference", name="contribution_discord", field=models.BooleanField(default=True)),
        migrations.CreateModel(
            name="NotificationSchedulerRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=40, default="owner_notices")),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(null=True, blank=True)),
                ("status", models.CharField(max_length=12, default="RUNNING",
                    choices=[("RUNNING", "Running"), ("SUCCEEDED", "Succeeded"), ("FAILED", "Failed")])),
                ("new_deliveries", models.PositiveIntegerField(default=0)),
                ("attempted_retries", models.PositiveIntegerField(default=0)),
                ("successful_retries", models.PositiveIntegerField(default=0)),
                ("error_code", models.CharField(max_length=40, blank=True, default="")),
            ],
            options={"ordering": ["-started_at", "-pk"]},
        ),
    ]
