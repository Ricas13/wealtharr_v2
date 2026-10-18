from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0007_signal_review")]
    operations = [
        migrations.AddField(
            model_name="account", name="country",
            field=models.CharField(max_length=2, default="GB"),
        ),
        migrations.AddField(
            model_name="account", name="broker",
            field=models.CharField(max_length=32, default="UNKNOWN"),
        ),
    ]
